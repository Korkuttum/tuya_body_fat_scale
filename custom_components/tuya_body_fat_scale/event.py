"""Event entity that fires every time a user is weighed."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import TuyaScaleDataUpdateCoordinator
from .helpers import clean_name

EVENT_WEIGHED = "weighed"
EVENT_DATA_KEYS = (
    "name", "weight", "bmi", "body_fat", "muscle_mass", "body_water",
    "visceral_fat", "body_score", "last_measurement",
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add a weighing event for each user that has data."""
    coordinator: TuyaScaleDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        TuyaScaleWeighedEvent(coordinator, config_entry, user_id, data.get("name", user_id))
        for user_id, data in (coordinator.data or {}).items()
    )


class TuyaScaleWeighedEvent(CoordinatorEntity, EventEntity):
    """Fires `weighed` when a newer measurement than the last seen one arrives.

    The measurements already present when the integration starts are the baseline
    and do not fire, so a restart never replays old weighings.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "weighed"
    _attr_event_types = [EVENT_WEIGHED]

    def __init__(
        self,
        coordinator: TuyaScaleDataUpdateCoordinator,
        config_entry: ConfigEntry,
        user_id: str,
        user_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._user_id = user_id
        self._attr_unique_id = f"{config_entry.entry_id}_{user_id}_weighed"
        self.entity_id = f"event.{DOMAIN}_{clean_name(user_name)}_weighed"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{user_id}")},
            name=f"{NAME} - {user_name}",
            manufacturer="Tuya",
            model="Body Fat Scale",
        )
        self._last_ts = self._measured_at()

    def _measured_at(self):
        data = (self.coordinator.data or {}).get(self._user_id) or {}
        return data.get("measured_at_ms") or None

    @callback
    def _handle_coordinator_update(self) -> None:
        ts = self._measured_at()
        if ts and (self._last_ts is None or ts > self._last_ts):
            self._last_ts = ts
            data = self.coordinator.data[self._user_id]
            self._trigger_event(EVENT_WEIGHED, {k: data.get(k) for k in EVENT_DATA_KEYS})
        super()._handle_coordinator_update()  # writes the state
