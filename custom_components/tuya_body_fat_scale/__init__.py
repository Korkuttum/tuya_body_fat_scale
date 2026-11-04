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
    CONF_SESSION,
    CONF_USER_CODE,
    DEFAULT_SCAN_INTERVAL,
)
from .sharing import ScaleSharingClient
from .coordinator import TuyaScaleDataUpdateCoordinator
from .api import TuyaScaleAPI

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]  # <-- BUTTON ekle

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tuya Body Fat Scale from a config entry."""
    try:
        _LOGGER.debug("Setting up Tuya Scale integration for device %s", entry.data.get("device_id"))
        
        # Create API instance
        api = TuyaScaleAPI(hass, dict(entry.data))

        # If this is first setup or migrating from old setup, ensure scan_interval is in options
        if CONF_SCAN_INTERVAL in entry.data and not entry.options.get(CONF_SCAN_INTERVAL):
            new_options = dict(entry.options)
            new_options[CONF_SCAN_INTERVAL] = entry.data[CONF_SCAN_INTERVAL]
            hass.config_entries.async_update_entry(entry, options=new_options)
            _LOGGER.debug("Migrated scan_interval to options: %s", new_options)

        # Create update coordinator with scan_interval from options
        coordinator = TuyaScaleDataUpdateCoordinator(
            hass,
            api,
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            entry,
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

        # Optional MQTT push (needs the QR session saved by the config flow)
        if entry.data.get(CONF_SESSION) and entry.data.get(CONF_USER_CODE):
            coordinator.sharing = ScaleSharingClient(
                hass,
                entry,
                coordinator.handle_measurement,
                coordinator.set_push_connected,
                on_resync=coordinator.resync,
            )
            # push not connected yet: poll at the capped fallback interval until it is
            coordinator.set_push_connected(False)
            await coordinator.sharing.async_start()
        else:
            _LOGGER.info("No QR session configured; using polling only")

        # Register update listener for options
        coordinator.options_snapshot = dict(entry.options)
        entry.async_on_unload(entry.add_update_listener(update_listener))

        return True

    except Exception as err:
        _LOGGER.error("Error setting up Tuya Scale integration: %s", err)
        raise ConfigEntryNotReady from err

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is not None:
        if coordinator.sharing is not None:
            await coordinator.sharing.async_stop()
        await coordinator.async_shutdown()
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when options changed (token refreshes also update the entry)."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if dict(entry.options) == coordinator.options_snapshot:
        return
    coordinator.options_snapshot = dict(entry.options)
    await hass.config_entries.async_reload(entry.entry_id)
