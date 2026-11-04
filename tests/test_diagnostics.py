"""The diagnostics download must not do blocking I/O in the event loop."""
import threading
from unittest.mock import patch

from custom_components.tuya_body_fat_scale import diagnostics
from custom_components.tuya_body_fat_scale.const import DOMAIN

from .test_init import make_entry, patches
from .test_sharing import FakeManager


async def test_sdk_version_is_read_outside_the_event_loop(hass):
    entry = make_entry(hass)
    p_api, p_client = patches(FakeManager())
    seen = {}

    def fake_version():
        seen["thread"] = threading.get_ident()
        return (0, 2, 15)

    with p_api, p_client:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        with patch.object(diagnostics, "sdk_version", fake_version):
            info = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
        assert info["sdk_version"] == "0.2.15"
        assert seen["thread"] != threading.get_ident()  # ran in the executor, not in the loop
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert DOMAIN in hass.data
