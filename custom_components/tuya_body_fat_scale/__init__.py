"""The Tuya Body Fat Scale integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL,
    CONF_SESSION,
    CONF_USER_CODE,
    DEFAULT_SCAN_INTERVAL,
    ISSUE_API_QUOTA,
    ISSUE_DEVICE_NOT_IN_ACCOUNT,
    ISSUE_SESSION_EXPIRED,
)
from .helpers import find_device
from .sharing import ScaleSharingClient
from .coordinator import TuyaScaleDataUpdateCoordinator
from .api import TuyaScaleAPI

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

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

        _remove_retired_entities(hass, entry)

        # Optional MQTT push (needs the QR session saved by the config flow).
        # The client is started after the platforms are set up.
        if entry.data.get(CONF_SESSION) and entry.data.get(CONF_USER_CODE):
            coordinator.sharing = ScaleSharingClient(
                hass,
                entry,
                coordinator.handle_measurement,
                coordinator.set_push_connected,
                on_resync=coordinator.resync,
                on_activity=coordinator.note_push_activity,
            )
            # push not connected yet: poll at the capped fallback interval until it is
            coordinator.set_push_connected(False)
        else:
            _LOGGER.info("No QR session configured; using polling only")

        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Platforms setup completed")

        if coordinator.sharing is not None:
            await coordinator.sharing.async_start()

        # Register update listener for options
        coordinator.options_snapshot = dict(entry.options)
        entry.async_on_unload(entry.add_update_listener(update_listener))

        return True

    except ConfigEntryAuthFailed:
        raise  # starts the re-authentication flow
    except Exception as err:
        _LOGGER.error("Error setting up Tuya Scale integration: %s", err)
        raise ConfigEntryNotReady from err

def _remove_retired_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the push-status and weighing-event entities (and their device) of 2.0.0-beta.4."""
    ent_reg = er.async_get(hass)
    retired = {f"{entry.entry_id}_push_connected", f"{entry.entry_id}_last_push_message"}
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity.unique_id in retired or entity.unique_id.endswith("_weighed"):
            ent_reg.async_remove(entity.entity_id)
    dev_reg = dr.async_get(hass)
    device = find_device(dev_reg, entry.entry_id, entry.entry_id)
    if device is not None:
        dev_reg.async_remove_device(device.id)


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

async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the Repairs issues of a removed entry."""
    for kind in (ISSUE_SESSION_EXPIRED, ISSUE_DEVICE_NOT_IN_ACCOUNT, ISSUE_API_QUOTA):
        ir.async_delete_issue(hass, DOMAIN, f"{kind}_{entry.entry_id}")


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when options changed (token refreshes also update the entry)."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if dict(entry.options) == coordinator.options_snapshot:
        return
    coordinator.options_snapshot = dict(entry.options)
    await hass.config_entries.async_reload(entry.entry_id)
