"""The Tuya Body Fat Scale integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import TuyaScaleDataUpdateCoordinator
from .api import TuyaScaleAPI

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tuya Body Fat Scale from a config entry."""
    try:
        _LOGGER.debug("Setting up Tuya Scale integration with config: %s", entry.data)
        
        # Create API instance
        api = TuyaScaleAPI(hass, dict(entry.data))

        # Create update coordinator
        coordinator = TuyaScaleDataUpdateCoordinator(
            hass,
            api,
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            entry,  # Pass the config_entry to coordinator
        )

        # Fetch initial data
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug("Initial data fetch completed: %s", coordinator.data)

        # Store coordinator
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = coordinator

        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Platforms setup completed")

        # Register update listener for options
        entry.async_on_unload(entry.add_update_listener(update_listener))

        return True

    except Exception as err:
        _LOGGER.error("Error setting up Tuya Scale integration: %s", err)
        raise ConfigEntryNotReady from err

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    await hass.config_entries.async_reload(entry.entry_id)
