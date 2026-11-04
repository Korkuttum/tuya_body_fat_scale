"""DataUpdateCoordinator for the Tuya Body Fat Scale integration.

Last updated: 2025-07-30 09:00:00 by Copilot
Changes:
- API hata durumunda kod görünümünde persistent_notification ekler (doğru servis çağrısıyla).
- Cache mekanizması ile son başarılı veri döndürülür.
- Bildirim ayarı kullanıcı seçimine bağlıdır.
"""

import asyncio
import logging
from datetime import datetime, timedelta
import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .api import TuyaScaleAPI
from .const import (
    DOMAIN,
    ERROR_AUTH,
    CONF_USERS,
    CONF_API_ERROR_NOTIFICATION,  # eklendi
    CONF_HEIGHT,
    ISSUE_API_QUOTA,
    MQTT_DEBOUNCE_SECONDS,
    MQTT_DOWN_MAX_INTERVAL,
    MQTT_FALLBACK_INTERVAL,
    MQTT_RETRIES,
    MQTT_RETRY_DELAY,
)

_LOGGER = logging.getLogger(__name__)

class TuyaScaleDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Tuya Scale data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: TuyaScaleAPI,
        update_interval: int,
        config_entry,
    ) -> None:
        """Initialize global Tuya Scale data updater."""
        self.api = api
        self.config_entry = config_entry
        self.user_data = config_entry.data.get(CONF_USERS, {})
        self._last_success_data = None
        self.hass = hass  # Bildirim göndermek için kaydediyoruz
        self._poll_interval = timedelta(seconds=update_interval)
        self._max_pages = 5
        self._latest_ts = 0  # newest record time seen for a configured user
        self._report_cache: dict = {}  # user_id -> (key, result)
        self._trigger_task: asyncio.Task | None = None
        self.push_connected = False
        self.sharing = None
        self.last_push_message = None  # datetime of the last status message from the scale

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

        _LOGGER.debug("Initialized coordinator with users: %s", self.user_data)

    def _process_resistance(self, resistance_value: str) -> int:
        try:
            resistance_float = float(resistance_value)
            if resistance_float < 1:
                return int(resistance_float * 1000)
            return int(resistance_float)
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid resistance value: %s", resistance_value)
            return 0

    @staticmethod
    def _positive_number(value) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def _resolve_height(self, record: dict, user_info: dict):
        """Height from the record, else the one saved for the user, else the old 170 cm default.

        The cloud sometimes reports the string "null"; that is only replaced by a saved height.
        """
        candidates = []
        if "height" in record:
            candidates.append(record["height"])
        candidates.append(user_info.get(CONF_HEIGHT))
        for candidate in candidates:
            height = self._positive_number(candidate)
            if height is not None:
                return int(height) if height == int(height) else height
        return None if "height" in record else 170

    def _has_usable_values(self, record: dict, user_info: dict) -> bool:
        """Weight and a height (from the record or the user settings) are required for the analysis."""
        weight = self._positive_number(record.get("weight", record.get("wegith", 0)))
        return weight is not None and self._resolve_height(record, user_info) is not None

    def _format_body_type_key(self, body_type: int) -> str:
        """Format body type as translation key (lowercase)."""
        types = {
            0: "underweight",
            1: "normal", 
            2: "overweight",
            3: "obese",
            4: "severely_obese"
        }
        return types.get(body_type, "normal")

    # -- MQTT push -----------------------------------------------------

    def set_push_connected(self, connected: bool) -> None:
        """Poll rarely while push works, at the configured interval otherwise."""
        was_connected = self.push_connected
        self.push_connected = connected
        self.update_interval = (
            timedelta(seconds=MQTT_FALLBACK_INTERVAL) if connected else self._fallback_interval()
        )
        if self._listeners:
            # the pending timer was set with the old interval
            self._schedule_refresh()
        if connected and not was_connected:
            # catch up on anything missed while the push connection was down
            self.resync()

    def note_push_activity(self) -> None:
        """The scale sent any status message over MQTT (shown in the diagnostics download)."""
        self.last_push_message = dt_util.utcnow()

    def _fallback_interval(self) -> timedelta:
        """Polling interval while push is not connected (capped when push is configured)."""
        if self.sharing is None:
            return self._poll_interval
        return min(self._poll_interval, timedelta(seconds=MQTT_DOWN_MAX_INTERVAL))

    def resync(self) -> None:
        """Fetch once to catch up on measurements a push gap may have swallowed."""
        self.hass.async_create_task(self._async_refresh_recent())

    async def _async_refresh_recent(self) -> None:
        """Refresh from the newest page of records only (cheap: one API call)."""
        self._max_pages = 1
        try:
            await self.async_refresh()
        finally:
            self._max_pages = 5

    def handle_measurement(self) -> None:
        """A push message says the scale reported something; refresh after it settles."""
        if self._trigger_task and not self._trigger_task.done():
            self._trigger_task.cancel()
        self._trigger_task = self.hass.async_create_task(self._refresh_after_measurement())

    async def _refresh_after_measurement(self) -> None:
        await asyncio.sleep(MQTT_DEBOUNCE_SECONDS)
        for attempt in range(MQTT_RETRIES + 1):
            before = self._latest_ts
            await self._async_refresh_recent()
            if self._latest_ts > before:
                return
            if attempt < MQTT_RETRIES:
                await asyncio.sleep(MQTT_RETRY_DELAY)
        _LOGGER.debug("No newer record after push message")

    async def async_shutdown(self) -> None:
        if self._trigger_task and not self._trigger_task.done():
            self._trigger_task.cancel()
        await super().async_shutdown()

    async def _async_update_data(self) -> dict:
        try:
            async with async_timeout.timeout(30):
                _LOGGER.debug("Starting data update")
                records = await self.api.get_scale_records(max_pages=self._max_pages)
                _LOGGER.debug("Received records: %s", records)

                user_records = {}
                for record in records.get("records", []):
                    user_id = record.get("user_id")
                    if not user_id or user_id not in self.user_data:
                        continue
                    if not self._has_usable_values(record, self.user_data[user_id]):
                        # e.g. weight reported as "null" or no height anywhere: analysis can't run
                        _LOGGER.debug("Skipping unusable record for user %s: %s", user_id, record)
                        continue

                    current_timestamp = record.get("create_time", 0)
                    if (user_id not in user_records or 
                        current_timestamp > user_records[user_id].get("create_time", 0)):
                        user_records[user_id] = record

                results = {}
                # newest record of anybody, also of users that are not set up: a weighing of
                # theirs must end the retries after a push message as well
                newest = max((r.get("create_time", 0) for r in records.get("records", [])), default=0)
                for user_id, record in user_records.items():
                    try:
                        user_info = self.user_data[user_id]
                        birth_date = datetime.strptime(user_info["birth_date"], "%d.%m.%Y")
                        age = (datetime.now() - birth_date).days // 365

                        weight = record.get("weight", record.get("wegith", 0))
                        height = self._resolve_height(record, user_info)
                        resistance = record.get("resistance", record.get("body_r", 0))
                        processed_resistance = self._process_resistance(resistance)

                        cache_key = (record.get("create_time"), age, height, weight, processed_resistance)
                        cached = self._report_cache.get(user_id)
                        if cached and cached[0] == cache_key:
                            results[user_id] = cached[1]
                            continue

                        analysis_data = {
                            "height": height,
                            "weight": weight,
                            "resistance": processed_resistance,
                            "age": age,
                            "sex": 1 if user_info["gender"] == "M" else 2
                        }

                        _LOGGER.debug("Getting analysis for user %s with data: %s", user_id, analysis_data)
                        report = await self.api.get_analysis_report(analysis_data)

                        results[user_id] = {
                            "user_id": user_id,
                            "name": user_info.get("name", record.get("nick_name", "Unknown")),
                            "birth_date": user_info["birth_date"],
                            "age": age,
                            "gender": "male" if user_info["gender"] == "M" else "female",  # DEĞİŞTİ: küçük harf
                            "height": height,
                            "weight": weight,
                            "resistance": processed_resistance,
                            "last_measurement": self.api.format_datetime(record.get("create_time", 0)),
                            "body_type": self._format_body_type_key(report.get("body_type", 0)),  # DEĞİŞTİ: key format
                            "fat_free_mass": report.get("ffm", 0),
                            "body_water": report.get("water", 0),
                            "body_score": report.get("body_score", 0),
                            "bone_mass": report.get("bones", 0),
                            "muscle_mass": report.get("muscle", 0),
                            "protein": report.get("protein", 0),
                            "body_fat": report.get("fat", 0),
                            "basal_metabolism": report.get("metabolism", 0),
                            "visceral_fat": report.get("visceral_fat", 0),
                            "body_age": report.get("body_age", 0),
                            "bmi": report.get("bmi", 0)
                        }
                        self._report_cache[user_id] = (cache_key, results[user_id])
                        _LOGGER.debug("Processed data for user %s: %s", user_id, results[user_id])
                    except Exception as err:
                        _LOGGER.error("Error processing user %s: %s", user_id, str(err))

                # a user missing from this fetch (short page, failed analysis) keeps the last values
                for user_id, previous in (self._last_success_data or {}).items():
                    if user_id in self.user_data:
                        results.setdefault(user_id, previous)

                self._latest_ts = max(self._latest_ts, newest)
                self._last_success_data = results
                ir.async_delete_issue(self.hass, DOMAIN, f"{ISSUE_API_QUOTA}_{self.config_entry.entry_id}")
                return results

        except Exception as err:
            _LOGGER.error("Error fetching data: %s", err)
            if "quota" in str(err).lower():
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"{ISSUE_API_QUOTA}_{self.config_entry.entry_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_API_QUOTA,
                    translation_placeholders={"error": str(err)},
                )
            # Bildirim ayarına göre gönder
            should_notify = (
                self.config_entry.options.get(CONF_API_ERROR_NOTIFICATION, True)
                if hasattr(self.config_entry, "options")
                else True
            )
            if should_notify:
                self.hass.async_create_task(
                    self.hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            "title": "Tuya Body Fat Scale API Uyarısı",
                            "message": f"```\n{str(err)}\n```"
                        },
                        blocking=False,
                    )
                )
            if "token" in str(err).lower():
                raise ConfigEntryAuthFailed(ERROR_AUTH)
            if self._last_success_data is not None:
                _LOGGER.warning("Returning cached data due to API failure.")
                return self._last_success_data
            raise UpdateFailed(f"Error communicating with API: {err}")
