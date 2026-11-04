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

from .api import TuyaScaleAPI
from .const import (
    DOMAIN,
    ERROR_AUTH,
    CONF_USERS,
    CONF_API_ERROR_NOTIFICATION,  # eklendi
    MQTT_DEBOUNCE_SECONDS,
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
    def _has_usable_values(record: dict) -> bool:
        """Height and weight must be positive numbers (the cloud sometimes sends "null")."""
        try:
            height = float(record.get("height", 170))
            weight = float(record.get("weight", record.get("wegith", 0)))
        except (TypeError, ValueError):
            return False
        return height > 0 and weight > 0

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
            timedelta(seconds=MQTT_FALLBACK_INTERVAL) if connected else self._poll_interval
        )
        if connected and not was_connected:
            # catch up on anything missed while the push connection was down
            self.hass.async_create_task(self.async_request_refresh())

    def handle_measurement(self) -> None:
        """A push message says the scale reported something; refresh after it settles."""
        if self._trigger_task and not self._trigger_task.done():
            self._trigger_task.cancel()
        self._trigger_task = self.hass.async_create_task(self._refresh_after_measurement())

    async def _refresh_after_measurement(self) -> None:
        await asyncio.sleep(MQTT_DEBOUNCE_SECONDS)
        for attempt in range(MQTT_RETRIES + 1):
            before = self._latest_ts
            self._max_pages = 1
            try:
                await self.async_refresh()
            finally:
                self._max_pages = 5
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
                    if not self._has_usable_values(record):
                        # e.g. height/weight reported as the string "null": analysis can't run
                        _LOGGER.debug("Skipping unusable record for user %s: %s", user_id, record)
                        continue

                    current_timestamp = record.get("create_time", 0)
                    if (user_id not in user_records or 
                        current_timestamp > user_records[user_id].get("create_time", 0)):
                        user_records[user_id] = record

                results = {}
                newest = max(
                    (r.get("create_time", 0) for r in user_records.values()), default=0
                )
                for user_id, record in user_records.items():
                    try:
                        user_info = self.user_data[user_id]
                        birth_date = datetime.strptime(user_info["birth_date"], "%d.%m.%Y")
                        age = (datetime.now() - birth_date).days // 365

                        weight = record.get("weight", record.get("wegith", 0))
                        height = record.get("height", 170)
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

                self._latest_ts = max(self._latest_ts, newest)
                self._last_success_data = results
                return results

        except Exception as err:
            _LOGGER.error("Error fetching data: %s", err)
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
