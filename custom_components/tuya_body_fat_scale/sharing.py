"""QR-session MQTT push client for the Tuya Body Fat Scale integration.

The scale is read through the user's Smart Life / Tuya Smart account (QR login,
``tuya-device-sharing-sdk``). The push message only tells us that a new
measurement exists; the numbers still come from the Tuya Cloud API.
"""
from __future__ import annotations

import importlib.metadata
import logging
import re
import time
from typing import Any, Callable

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from tuya_sharing import Manager, SharingTokenListener

try:  # raised by the SDK when Tuya rejects a request (expired/invalid token, ...)
    from tuya_sharing.exceptions import ApiRequestException
except ImportError:  # pragma: no cover - very old SDK

    class ApiRequestException(Exception):  # type: ignore[no-redef]
        """Placeholder so isinstance checks keep working."""

from .const import (
    DOMAIN,
    ISSUE_DEVICE_NOT_IN_ACCOUNT,
    ISSUE_SESSION_EXPIRED,
    REAUTH_SESSION,
    REAUTH_TARGET,
    SESSION_REAUTH_AFTER_FAILURES,
    SESSION_REAUTH_AFTER_SECONDS,
    CONF_DEVICE_ID,
    CONF_SESSION,
    CONF_USER_CODE,
    MEASUREMENT_DP_CODES,
    MEASUREMENT_DP_IDS,
    MQTT_HEALTH_INTERVAL,
    MQTT_MAX_RESTART_BACKOFF,
    MQTT_RESTART_AFTER_CHECKS,
    SDK_RECOMMENDED_VERSION,
    SESSION_TOKEN_KEYS,
    SHARING_CLIENT_ID,
)

_LOGGER = logging.getLogger(__name__)


class DeviceNotInAccount(Exception):
    """The configured device is not part of the QR-linked account."""


def is_device_message(msg: dict[str, Any], device_id: str) -> bool:
    """Return True for any status message of the device (not only weighings)."""
    return msg.get("protocol") == 4 and (msg.get("data") or {}).get("devId") == device_id


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


def sdk_version() -> tuple[int, ...] | None:
    """Installed tuya-device-sharing-sdk version as a tuple, if it can be read."""
    try:
        raw = importlib.metadata.version("tuya-device-sharing-sdk")
    except importlib.metadata.PackageNotFoundError:
        return None
    parts = re.findall(r"\d+", raw)[:3]
    return tuple(int(p) for p in parts) if parts else None


def warn_if_old_sdk() -> None:
    """Log the SDK version; older ones lack its own reconnect logic (blocking)."""
    version = sdk_version()
    _LOGGER.info("tuya-device-sharing-sdk version: %s", version)
    if version is not None and version < SDK_RECOMMENDED_VERSION:
        _LOGGER.warning(
            "tuya-device-sharing-sdk %s is older than %s; MQTT reconnects rely on this "
            "integration's health check (up to ~3 minutes) instead of the SDK's own logic",
            ".".join(map(str, version)),
            ".".join(map(str, SDK_RECOMMENDED_VERSION)),
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
        on_resync: Callable[[], None] | None = None,
        on_activity: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._on_measurement = on_measurement
        self._on_connection_change = on_connection_change
        self._on_resync = on_resync
        self._on_activity = on_activity
        self._seen_client: Any = None
        self._api_rejections = 0
        self._first_rejection = 0.0
        self._reauth_started = False
        self._manager_factory = manager_factory
        self._device_id: str = entry.data[CONF_DEVICE_ID]
        self._manager: Any = None
        self._connected = False
        self._unhealthy = 0
        self._failures = 0
        self._next_attempt = 0.0
        self._busy = False
        self._stopped = False
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def async_start(self) -> None:
        """Start listening. Never raises: failures fall back to polling."""
        self._stopped = False
        await self.hass.async_add_executor_job(warn_if_old_sdk)
        await self._async_connect()
        self._unsub = async_track_time_interval(
            self.hass, self._async_health_check, timedelta(seconds=MQTT_HEALTH_INTERVAL)
        )

    async def async_stop(self) -> None:
        self._stopped = True
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
        try:
            manager.mq.add_message_listener(self._handle_message)
        except Exception:
            self._stop_manager(manager)  # don't leave the MQTT thread running
            raise
        return manager

    @staticmethod
    def _stop_manager(manager: Any) -> None:
        try:
            if getattr(manager, "mq", None) is not None:
                manager.mq.stop()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Error stopping MQTT", exc_info=True)

    async def _async_connect(self) -> None:
        if self._busy or self._stopped:
            return
        self._busy = True
        try:
            old, self._manager = self._manager, None
            self._seen_client = None
            if old is not None:
                await self.hass.async_add_executor_job(self._stop_manager, old)
            manager = await self.hass.async_add_executor_job(self._connect_blocking)
            if self._stopped:
                # unloaded while connecting: nothing may keep running
                await self.hass.async_add_executor_job(self._stop_manager, manager)
                return
            self._manager = manager
            self._failures = 0
            self._unhealthy = 0
            self._api_rejections = 0
            self._reauth_started = False
            self._clear_issues()
        except DeviceNotInAccount:
            _LOGGER.error(
                "Device %s is not in the QR-linked account; falling back to polling",
                self._device_id,
            )
            self._create_issue(ISSUE_DEVICE_NOT_IN_ACCOUNT, ir.IssueSeverity.ERROR)
            self._schedule_backoff()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not start scale push (%s); using polling", err)
            if isinstance(err, ApiRequestException):
                # Tuya answered but refused: the QR session is most likely expired/revoked.
                # Network errors are not counted, they say nothing about the session.
                now = time.monotonic()
                if self._api_rejections == 0:
                    self._first_rejection = now
                self._api_rejections += 1
                if (
                    self._api_rejections >= SESSION_REAUTH_AFTER_FAILURES
                    and now - self._first_rejection >= SESSION_REAUTH_AFTER_SECONDS
                ):
                    self._start_reauth()
            self._schedule_backoff()
        finally:
            self._busy = False

    def _issue_id(self, kind: str) -> str:
        return f"{kind}_{self.entry.entry_id}"

    def _create_issue(self, kind: str, severity: ir.IssueSeverity) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(kind),
            is_fixable=False,
            severity=severity,
            translation_key=kind,
            translation_placeholders={"device_id": self._device_id},
        )

    def _clear_issues(self) -> None:
        for kind in (ISSUE_SESSION_EXPIRED, ISSUE_DEVICE_NOT_IN_ACCOUNT):
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(kind))

    def _start_reauth(self) -> None:
        """Ask the user to scan a new QR code (once per outage)."""
        if self._reauth_started:
            return
        self._reauth_started = True
        _LOGGER.error(
            "The Tuya QR session was rejected %d times in a row; asking for a new QR login",
            self._api_rejections,
        )
        self._create_issue(ISSUE_SESSION_EXPIRED, ir.IssueSeverity.ERROR)
        self.entry.async_start_reauth(self.hass, context={REAUTH_TARGET: REAUTH_SESSION})

    def _schedule_backoff(self) -> None:
        self._failures += 1
        delay = min(MQTT_HEALTH_INTERVAL * self._failures, MQTT_MAX_RESTART_BACKOFF)
        self._next_attempt = time.monotonic() + delay

    async def _async_health_check(self, _now: Any = None) -> None:
        if self._busy or self._stopped:
            return
        if self._manager is None:
            if time.monotonic() >= self._next_attempt:
                await self._async_connect()
            self._set_connected(False)
            return
        healthy = mqtt_connected(self._manager)
        was_connected = self._connected
        self._set_connected(healthy)
        if healthy:
            self._unhealthy = 0
            self._check_silent_reconnect(was_connected)
            return
        self._unhealthy += 1
        if self._unhealthy >= MQTT_RESTART_AFTER_CHECKS and time.monotonic() >= self._next_attempt:
            _LOGGER.info("Scale push unhealthy for %d checks, rebuilding", self._unhealthy)
            await self._async_connect()

    def _check_silent_reconnect(self, was_connected: bool) -> None:
        """The SDK swaps its MQTT client on every reconnect (drop or 2 h renewal).

        A drop shorter than one health-check interval is never seen as "down", yet a
        weighing during it is lost (no persistent MQTT session). A changed client
        object reveals the reconnect, so fetch once to catch up.
        """
        client = getattr(getattr(self._manager, "mq", None), "client", None)
        if client is None:
            return
        if self._seen_client is not None and client is not self._seen_client and was_connected:
            _LOGGER.debug("Scale push reconnected between checks; catching up")
            if self._on_resync:
                self._on_resync()
        self._seen_client = client

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """Called from the MQTT thread."""
        if is_device_message(msg, self._device_id) and self._on_activity:
            self.hass.loop.call_soon_threadsafe(self._on_activity)
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
