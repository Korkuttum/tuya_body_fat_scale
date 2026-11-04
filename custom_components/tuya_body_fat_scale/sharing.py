"""QR-session MQTT push client for the Tuya Body Fat Scale integration.

The scale is read through the user's Smart Life / Tuya Smart account (QR login,
``tuya-device-sharing-sdk``). The push message only tells us that a new
measurement exists; the numbers still come from the Tuya Cloud API.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from tuya_sharing import Manager, SharingTokenListener

from .const import (
    CONF_DEVICE_ID,
    CONF_SESSION,
    CONF_USER_CODE,
    MEASUREMENT_DP_CODES,
    MEASUREMENT_DP_IDS,
    MQTT_HEALTH_INTERVAL,
    MQTT_MAX_RESTART_BACKOFF,
    MQTT_RESTART_AFTER_CHECKS,
    SESSION_TOKEN_KEYS,
    SHARING_CLIENT_ID,
)

_LOGGER = logging.getLogger(__name__)


class DeviceNotInAccount(Exception):
    """The configured device is not part of the QR-linked account."""


def is_measurement_message(msg: dict[str, Any], device_id: str) -> bool:
    """Return True if a raw MQTT message reports a new weighing for the device."""
    if msg.get("protocol") != 4:
        return False
    data = msg.get("data") or {}
    if data.get("devId") != device_id:
        return False
    for item in data.get("status") or []:
        if item.get("dpId") in MEASUREMENT_DP_IDS or item.get("code") in MEASUREMENT_DP_CODES:
            return True
    return False


def session_token(info: dict[str, Any]) -> dict[str, Any]:
    """Keep only the token fields of a login result / refreshed token."""
    return {k: info[k] for k in SESSION_TOKEN_KEYS if k in info}


def build_manager(session: dict[str, Any], user_code: str, listener: Any = None):
    """Create a tuya_sharing Manager from a stored session (blocking)."""
    return Manager(
        SHARING_CLIENT_ID,
        user_code,
        session["terminal_id"],
        session["endpoint"],
        session["token"],
        listener,
    )


def mqtt_connected(manager: Any) -> bool:
    """Whether the SDK's MQTT thread is alive and the broker connection is up."""
    mq = getattr(manager, "mq", None)
    if mq is None or not mq.is_alive():
        return False
    client = getattr(mq, "client", None)
    return bool(client is not None and client.is_connected())


class ScaleSharingClient:
    """Keeps an MQTT connection to the scale and reports measurements."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        on_measurement: Callable[[], None],
        on_connection_change: Callable[[bool], None],
        manager_factory: Callable[..., Any] = build_manager,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._on_measurement = on_measurement
        self._on_connection_change = on_connection_change
        self._manager_factory = manager_factory
        self._device_id: str = entry.data[CONF_DEVICE_ID]
        self._manager: Any = None
        self._connected = False
        self._unhealthy = 0
        self._failures = 0
        self._next_attempt = 0.0
        self._busy = False
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def async_start(self) -> None:
        """Start listening. Never raises: failures fall back to polling."""
        await self._async_connect()
        self._unsub = async_track_time_interval(
            self.hass, self._async_health_check, timedelta(seconds=MQTT_HEALTH_INTERVAL)
        )

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        manager, self._manager = self._manager, None
        self._set_connected(False)
        if manager is not None:
            await self.hass.async_add_executor_job(self._stop_manager, manager)

    # -- internals ---------------------------------------------------------

    def _set_connected(self, value: bool) -> None:
        if value != self._connected:
            self._connected = value
            _LOGGER.info("Scale push connection %s", "established" if value else "lost")
            self._on_connection_change(value)

    def _connect_blocking(self) -> Any:
        session = self.entry.data[CONF_SESSION]
        manager = self._manager_factory(
            session, self.entry.data[CONF_USER_CODE], _TokenListener(self)
        )
        manager.update_device_cache()
        device = manager.device_map.get(self._device_id)
        if device is None:
            raise DeviceNotInAccount(self._device_id)
        # refresh_mq only subscribes to devices flagged set_up; subscribe to the scale only.
        device.set_up = True
        manager.refresh_mq()
        manager.mq.add_message_listener(self._handle_message)
        return manager

    @staticmethod
    def _stop_manager(manager: Any) -> None:
        try:
            if getattr(manager, "mq", None) is not None:
                manager.mq.stop()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Error stopping MQTT", exc_info=True)

    async def _async_connect(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            old, self._manager = self._manager, None
            if old is not None:
                await self.hass.async_add_executor_job(self._stop_manager, old)
            self._manager = await self.hass.async_add_executor_job(self._connect_blocking)
            self._failures = 0
            self._unhealthy = 0
        except DeviceNotInAccount:
            _LOGGER.error(
                "Device %s is not in the QR-linked account; falling back to polling",
                self._device_id,
            )
            self._schedule_backoff()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not start scale push (%s); using polling", err)
            self._schedule_backoff()
        finally:
            self._busy = False

    def _schedule_backoff(self) -> None:
        self._failures += 1
        delay = min(MQTT_HEALTH_INTERVAL * self._failures, MQTT_MAX_RESTART_BACKOFF)
        self._next_attempt = time.monotonic() + delay

    async def _async_health_check(self, _now: Any = None) -> None:
        if self._busy:
            return
        if self._manager is None:
            if time.monotonic() >= self._next_attempt:
                await self._async_connect()
            self._set_connected(False)
            return
        healthy = mqtt_connected(self._manager)
        self._set_connected(healthy)
        if healthy:
            self._unhealthy = 0
            return
        self._unhealthy += 1
        if self._unhealthy >= MQTT_RESTART_AFTER_CHECKS and time.monotonic() >= self._next_attempt:
            _LOGGER.info("Scale push unhealthy for %d checks, rebuilding", self._unhealthy)
            await self._async_connect()

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """Called from the MQTT thread."""
        if is_measurement_message(msg, self._device_id):
            self.hass.loop.call_soon_threadsafe(self._on_measurement)

    def _persist_token(self, token_info: dict[str, Any]) -> None:
        """Store a refreshed token, keeping endpoint and terminal_id."""
        session = dict(self.entry.data[CONF_SESSION])
        session["token"] = session_token(token_info)
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_SESSION: session}
        )


class _TokenListener(SharingTokenListener):
    """Persists tokens the SDK refreshes on its own."""

    def __init__(self, client: ScaleSharingClient) -> None:
        self._client = client

    def update_token(self, token_info: dict[str, Any]) -> None:
        info = dict(token_info)
        self._client.hass.loop.call_soon_threadsafe(self._client._persist_token, info)
