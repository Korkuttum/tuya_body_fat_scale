"""Support for Tuya Body Fat Scale buttons."""
from __future__ import annotations
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    NAME,
)
from .coordinator import TuyaScaleDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Body Fat Scale buttons."""
    # Coordinator'ın hazır olup olmadığını kontrol et
    if DOMAIN not in hass.data or config_entry.entry_id not in hass.data[DOMAIN]:
        _LOGGER.warning("Coordinator not ready, delaying button setup")
        return
    
    coordinator: TuyaScaleDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    # Coordinator'ın verisi yoksa bekle (ilk refresh yapılmamış olabilir)
    if not coordinator.data:
        _LOGGER.debug("Coordinator data not available yet, delaying button setup")
        return
    
    entities = []
    
    _LOGGER.debug("Setting up refresh buttons for all users")
    
    # Her kullanıcı için ayrı buton oluştur
    for user_id, user_data in coordinator.data.items():
        user_name = user_data.get('name', 'Unknown')
        _LOGGER.debug("Creating refresh button for user: %s", user_name)
        
        button = TuyaScaleRefreshButton(
            coordinator,
            config_entry,
            user_id,
            user_name,
        )
        entities.append(button)
    
    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Created %d refresh buttons", len(entities))
    else:
        _LOGGER.warning("No users found for creating buttons")

class TuyaScaleRefreshButton(ButtonEntity):
    """Refresh button for a specific user (but refreshes ALL data)."""
    
    def __init__(
        self,
        coordinator: TuyaScaleDataUpdateCoordinator,
        config_entry: ConfigEntry,
        user_id: str,
        user_name: str,
    ) -> None:
        """Initialize the button."""
        self.coordinator = coordinator
        self._config_entry = config_entry
        self._user_id = user_id
        self._user_name = user_name
        
        # Clean name for entity_id
        clean_name = self._clean_name(user_name)
        
        # Unique ID
        self._attr_unique_id = f"{config_entry.entry_id}_{user_id}_refresh"
        
        # Entity ID
        self.entity_id = f"button.{DOMAIN}_{clean_name}_refresh"
        
        # Button properties - translation key ekle
        self._attr_translation_key = "refresh"
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:refresh"
        
        # Device info - bu buton ilgili kullanıcının cihazının altında görünsün
        device_name = f"{NAME} - {user_name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{user_id}")},
            name=device_name,
            manufacturer="Tuya",
            model="Body Fat Scale"
        )
        
        _LOGGER.debug("Button created for user %s (%s)", user_name, user_id)

    def _clean_name(self, name: str) -> str:
        """Clean name for use in entity_id."""
        import re
        # Türkçe karakterleri düzelt
        replacements = {
            'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c',
            'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c',
            ' ': '_'
        }
        
        for old, new in replacements.items():
            name = name.replace(old, new)
        
        clean = re.sub(r'[^a-z0-9_]', '_', name.lower())
        clean = re.sub(r'_+', '_', clean)
        return clean.strip('_')

    async def async_press(self) -> None:
        """Handle the button press - refreshes ALL user data."""
        _LOGGER.info("Manual refresh requested by %s (refreshing ALL data)", self._user_name)
        
        # Coordinator'ı yenile
        await self.coordinator.async_refresh()
        
        _LOGGER.info("All scale data refreshed (triggered by %s)", self._user_name)

    @property
    def available(self) -> bool:
        """Return if button is available."""
        # Buton her zaman available olsun
        return True