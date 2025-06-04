"""Support for Tuya Body Fat Scale sensors."""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta  # timedelta'yı ekledik

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfMass,
    PERCENTAGE,
    UnitOfLength,
    UnitOfEnergy,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import (
    DOMAIN,
    NAME,
    SENSOR_TYPES,
)
from .coordinator import TuyaScaleDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Body Fat Scale sensors."""
    coordinator: TuyaScaleDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    entities = []
    
    _LOGGER.debug("Setting up sensors. Coordinator data: %s", coordinator.data)
    
    # Create sensor entities for each user and each sensor type
    for user_id, user_data in coordinator.data.items():
        device_name = f"{NAME} - {user_data.get('name', 'Unknown')}"
        _LOGGER.debug("Creating sensors for user %s with name %s", user_id, device_name)
        
        for sensor_key, sensor_config in SENSOR_TYPES.items():
            _LOGGER.debug("Creating sensor %s for user %s", sensor_key, user_id)
            
            entities.append(
                TuyaScaleSensor(
                    coordinator,
                    config_entry,
                    user_id,
                    sensor_key,
                    device_name,
                )
            )
    
    _LOGGER.debug("Created %d sensor entities", len(entities))
    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.warning("No sensor entities were created!")

class TuyaScaleSensor(CoordinatorEntity, SensorEntity):
    """Implementation of a Tuya Body Fat Scale sensor."""

    def __init__(
        self,
        coordinator: TuyaScaleDataUpdateCoordinator,
        config_entry: ConfigEntry,
        user_id: str,
        sensor_key: str,
        device_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._config_entry = config_entry
        self._user_id = user_id
        self._sensor_key = sensor_key
        self._device_name = device_name
        self._attr_has_entity_name = True
        
        # Set up unique ID
        self._attr_unique_id = f"{config_entry.entry_id}_{user_id}_{sensor_key}"
        
        # Set device info - via_device removed for 2025.12 compatibility
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{user_id}")},
            name=device_name,
            manufacturer="Tuya",
            model="Body Fat Scale"
        )
        
        # Set sensor specific attributes
        sensor_info = SENSOR_TYPES[sensor_key]
        self._attr_device_class = sensor_info.get("device_class")
        self._attr_state_class = sensor_info.get("state_class")
        self._attr_icon = sensor_info.get("icon")
        self._attr_name = sensor_info.get("name")

    @property
    def available(self) -> bool:
        """Return if sensor is available."""
        if not self.coordinator.last_update_success:
            return False
        if self._user_id not in self.coordinator.data:
            return False
        return True

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if not self.available:
            return None
        
        try:
            user_data = self.coordinator.data[self._user_id]
            value = user_data.get(self._sensor_key)
        
            # Format specific values
            if self._sensor_key == "last_measurement":
                try:
                    # Parse string to datetime if it's a string
                    if isinstance(value, str):
                        local_dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                        # Convert to UTC by subtracting 3 hours
                        utc_dt = local_dt - timedelta(hours=3)
                        return utc_dt.replace(tzinfo=timezone.utc)
                    return None
                except (ValueError, TypeError):
                    return None
                
            return value
        except Exception as err:
            _LOGGER.error(
                "Error getting value for sensor %s (user %s): %s",
                self._sensor_key,
                self._user_id,
                err,
            )
            return None

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        attrs = {}
        
        if self.available:
            user_data = self.coordinator.data[self._user_id]
            
            # Add common attributes
            attrs["user_name"] = user_data.get("name")
            attrs["user_id"] = self._user_id
            
            # Add measure-specific attributes
            if self._sensor_key in ["weight", "bmi", "body_fat"]:
                attrs["height"] = user_data.get("height")
                attrs["age"] = user_data.get("age")
                attrs["gender"] = user_data.get("gender")
                
            # Add last_measurement to all sensors except itself
            if self._sensor_key != "last_measurement":
                attrs["last_measurement"] = user_data.get("last_measurement")
            
        return attrs

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        if self._sensor_key in ["weight", "bone_mass", "muscle_mass", "fat_free_mass"]:
            return UnitOfMass.KILOGRAMS
        elif self._sensor_key in ["body_water", "protein", "body_fat"]:
            return PERCENTAGE
        elif self._sensor_key == "height":
            return UnitOfLength.CENTIMETERS
        elif self._sensor_key == "basal_metabolism":
            return UnitOfEnergy.KILO_CALORIE
        return None

    @property
    def device_class(self) -> str | None:
        """Return the device class."""
        if self._sensor_key == "last_measurement":
            return SensorDeviceClass.TIMESTAMP
        elif self._sensor_key in ["weight", "bone_mass", "muscle_mass", "fat_free_mass"]:
            return SensorDeviceClass.WEIGHT
        return None

    @property
    def state_class(self) -> str | None:
        """Return the state class."""
        if self._sensor_key in ["weight", "bmi", "body_fat", "body_water", "protein",
                              "bone_mass", "muscle_mass", "fat_free_mass", "basal_metabolism"]:
            return SensorStateClass.MEASUREMENT
        return None
