"""DataUpdateCoordinator for the Tuya Body Fat Scale integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import TuyaScaleAPI
from .const import (
    DOMAIN,
    ERROR_AUTH,
    CONF_USERS,
)

_LOGGER = logging.getLogger(__name__)

class TuyaScaleDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Tuya Scale data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: TuyaScaleAPI,
        update_interval: int,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize global Tuya Scale data updater."""
        self.api = api
        self.config_entry = config_entry
        self.user_data = config_entry.data.get(CONF_USERS, {})

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

        _LOGGER.debug("Initialized coordinator with users: %s", self.user_data)

    async def _async_update_data(self) -> dict:
        """Fetch data from Tuya Scale API."""
        try:
            async with async_timeout.timeout(60):  # Timeout süresini artırdık çünkü daha fazla sayfa çekeceğiz
                _LOGGER.debug("Starting data update")
                
                # Get scale records from multiple pages
                records = await self.api.get_scale_records(max_pages=5)  # 5 sayfa geriye gidelim
                _LOGGER.debug("Received total records: %s", len(records.get("records", [])))
                
                # Process records
                user_records = {}
                seen_users = set()
                
                # Tüm kayıtları işle ve her kullanıcı için en son kaydı bul
                for record in records.get("records", []):
                    user_id = record.get("user_id")
                    if not user_id:
                        continue
                        
                    # API'den gelen timestamp'i kullan
                    current_timestamp = record.get("create_time", 0)
                    
                    # Eğer bu kullanıcıyı ilk kez görüyorsak veya daha yeni bir kayıtsa
                    if user_id not in seen_users:
                        seen_users.add(user_id)
                        user_records[user_id] = record
                    elif current_timestamp > user_records[user_id].get("create_time", 0):
                        user_records[user_id] = record

                # Get analysis reports for each user
                results = {}
                
                # Tüm kayıtlı kullanıcılar için işlem yap
                for user_id in self.user_data:
                    try:
                        # Kullanıcı için kayıt var mı kontrol et
                        if user_id in user_records:
                            record = user_records[user_id]
                            create_time = record.get("create_time", 0)
                            _LOGGER.debug("Found data for user %s, timestamp: %s", 
                                        user_id, self.api.format_datetime(create_time))
                        else:
                            _LOGGER.warning("No data found for user %s in any page", user_id)
                            continue

                        user_info = self.user_data[user_id]
                        birth_date = datetime.strptime(user_info["birth_date"], "%d.%m.%Y")
                        age = (datetime.now() - birth_date).days // 365

                        # API'den gelen değerleri kontrol et ve varsayılan değerler kullan
                        weight = record.get("weight", record.get("wegith", 0))
                        height = record.get("height", 170)  # varsayılan boy
                        resistance = record.get("resistance", record.get("body_r", 0))

                        analysis_data = {
                            "height": height,
                            "weight": weight,
                            "resistance": resistance,
                            "age": age,
                            "sex": 1 if user_info["gender"] == "M" else 2
                        }

                        _LOGGER.debug("Getting analysis for user %s with data: %s", user_id, analysis_data)
                        
                        # Get analysis report
                        report = await self.api.get_analysis_report(analysis_data)
                        
                        # Combine all data
                        results[user_id] = {
                            "user_id": user_id,
                            "name": user_info.get("name", record.get("nick_name", "Unknown")),
                            "birth_date": user_info["birth_date"],
                            "age": age,
                            "gender": "male" if user_info["gender"] == "M" else "female",
                            "height": height,
                            "weight": weight,
                            "resistance": resistance,
                            "last_measurement": self.api.format_datetime(record.get("create_time", 0)),
                            "body_type": self.api.format_body_type(report.get("body_type", 0)),
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
                        
                        _LOGGER.debug("Processed data for user %s: %s", user_id, results[user_id])
                    except Exception as err:
                        _LOGGER.error("Error processing user %s: %s", user_id, str(err))

                if not results:
                    _LOGGER.warning("No data was processed for any user")
                else:
                    _LOGGER.debug("Successfully processed data for %d users", len(results))

                return results

        except Exception as err:
            if "token" in str(err).lower():
                raise ConfigEntryAuthFailed(ERROR_AUTH)
            raise UpdateFailed(f"Error communicating with API: {err}")
