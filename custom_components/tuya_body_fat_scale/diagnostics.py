"""Diagnostics download (secrets redacted)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_ID,
    CONF_ACCESS_KEY,
    CONF_DEVICE_ID,
    CONF_SESSION,
    CONF_USER_CODE,
    CONF_USERS,
    DOMAIN,
)
from .sharing import sdk_version

TO_REDACT = {CONF_ACCESS_ID, CONF_ACCESS_KEY, CONF_USER_CODE, CONF_SESSION, CONF_DEVICE_ID}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = dict(entry.data)
    users = data.pop(CONF_USERS, {})
    info: dict[str, Any] = {
        "entry": async_redact_data(data, TO_REDACT),
        "options": dict(entry.options),
        "has_qr_session": bool(entry.data.get(CONF_SESSION)),
        "users": len(users),
        "sdk_version": ".".join(map(str, await hass.async_add_executor_job(sdk_version) or ())) or None,
    }
    if coordinator is not None:
        info["coordinator"] = {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "users_with_data": len(coordinator.data or {}),
            "push_configured": coordinator.sharing is not None,
            "push_connected": coordinator.push_connected,
            "last_push_message": coordinator.last_push_message.isoformat()
            if coordinator.last_push_message
            else None,
        }
    return info
