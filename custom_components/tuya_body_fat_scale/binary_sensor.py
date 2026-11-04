"""Diagnostic binary sensor: is the MQTT push connection up?"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TuyaScaleDataUpdateCoordinator
from .helpers import scale_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the push connection sensor when MQTT push is configured."""
    coordinator: TuyaScaleDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    if coordinator.sharing is not None:
        async_add_entities([TuyaScalePushConnectedSensor(coordinator, config_entry)])


class TuyaScalePushConnectedSensor(CoordinatorEntity, BinarySensorEntity):
    """On while the MQTT connection to the scale is healthy."""

    _attr_has_entity_name = True
    _attr_translation_key = "push_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TuyaScaleDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{config_entry.entry_id}_push_connected"
        self._attr_device_info = scale_device_info(config_entry.entry_id)

    @property
    def available(self) -> bool:
        # Meaningful even when the last API refresh failed.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.push_connected
