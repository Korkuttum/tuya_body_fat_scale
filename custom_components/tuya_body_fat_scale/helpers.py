"""Small shared helpers."""
from __future__ import annotations

import re

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, NAME

_TURKISH = str.maketrans("ığüşöçİĞÜŞÖÇ", "igusocigusoc")


def clean_name(name: str) -> str:
    """Make a user name usable inside an entity_id (Turkish letters folded to ASCII)."""
    clean = re.sub(r"[^a-z0-9_]", "_", name.translate(_TURKISH).lower())
    return re.sub(r"_+", "_", clean).strip("_")


def scale_device_info(entry_id: str) -> DeviceInfo:
    """Device that groups the scale-wide diagnostic entities (push state, last message)."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=NAME,
        manufacturer="Tuya",
        model="Body Fat Scale",
    )
