"""Small shared helpers."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr

from .const import DOMAIN


def find_device(dev_reg: dr.DeviceRegistry, entry_id: str, identifier: str) -> dr.DeviceEntry | None:
    """Device of this config entry with the identifier (DOMAIN, identifier).

    Avoids ``async_get_device(identifiers=...)``, which Home Assistant deprecated because
    identifiers are no longer unique across config entries.
    """
    for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
        if (DOMAIN, identifier) in device.identifiers:
            return device
    return None
