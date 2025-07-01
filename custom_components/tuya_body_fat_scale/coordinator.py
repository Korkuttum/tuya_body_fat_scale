"""DataUpdateCoordinator for the Tuya Body Fat Scale integration.

Last updated: 2025-07-01 06:58:28 by Korkuttum
Changes:
- Added resistance value processing to handle both decimal and integer formats
- Added _process_resistance helper function
"""
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

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

        _LOGGER.debug("Initialized coordinator with users: %s", self.user_data)

    def _process_resistance(self, resistance_value: str) -> int:
        """Process resistance value based on its format.
        
        Args:
            resistance_value: The resistance value from the API as string
            
        Returns:
            int: Processed resistance value
            - If value < 1 (e.g., "0.756"), returns value * 1000
            - If value >= 1 (e.g., "575"), returns value as is
            - Returns 0 if value is invalid
        """
        try:
            # String'i float'a çevir
            resistance_float = float(resistance_value)
            
            # 1'den küçükse 1000 ile çarp
            if resistance_float < 1:
                return int(resistance_float * 1000)
            # 1'den büyük veya eşitse direkt int'e çevir
            return int(resistance_float)
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid resistance value: %s", resistance_value)
            return 0

    async def _async_update_data(self) -> dict:
        """Fetch data from Tuya Scale API."""
        try:
            async with async_timeout.timeout(30):
                _LOGGER.debug("Starting data update")
                
                # Get scale records
                records = await self.api.get_scale_records()
                _LOGGER.debug("Received records: %s", records)
                
                # Process records
                user_records = {}
                for record in records.get("records", []):
                    user_id = record.get("user_id")
                    if not user_id or user_id not in self.user_data:
                        continue

                    # API'den gelen timestamp'i kullan
                    current_timestamp = record.get("create_time", 0)
                    
                    # Update only if this is a newer record
                    if (user_id not in user_records or 
                        current_timestamp > user_records[user_id].get("create_time", 0)):
                        user_records[user_id] = record

                # Get analysis reports for each user
                results = {}
                for user_id, record in user_records.items():
                    try:
                        user_info = self.user_data[user_id]
                        birth_date = datetime.strptime(user_info["birth_date"], "%d.%m.%Y")
                        age = (datetime.now() - birth_date).days // 365

                        # API'den gelen değerleri kontrol et ve varsayılan değerler kullan
                        weight = record.get("weight", record.get("wegith", 0))
                        height = record.get("height", 170)  # varsayılan boy
                        resistance = record.get("resistance", record.get("body_r", 0))
                        
                        # Direnç değerini işle
                        processed_resistance = self._process_resistance(resistance)

                        analysis_data = {
                            "height": height,
                            "weight": weight,
                            "resistance": processed_resistance,  # İşlenmiş değeri kullan
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
                            "gender": "Male" if user_info["gender"] == "M" else "Female",
                            "height": height,
                            "weight": weight,
                            "resistance": processed_resistance,  # İşlenmiş değeri kullan
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

                return results

        except Exception as err:
            if "token" in str(err).lower():
                raise ConfigEntryAuthFailed(ERROR_AUTH)
            raise UpdateFailed(f"Error communicating with API: {err}")
