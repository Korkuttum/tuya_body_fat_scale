"""While MQTT is connected the Tuya API is not polled; catch-up requests only when needed."""
from datetime import timedelta
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_body_fat_scale import coordinator as coord_mod
from custom_components.tuya_body_fat_scale.const import CONF_USERS, DOMAIN
from custom_components.tuya_body_fat_scale.coordinator import TuyaScaleDataUpdateCoordinator

from .test_coordinator import USERS, FakeApi, record


def make(hass, api, interval=300):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USERS: USERS}, options={})
    entry.add_to_hass(hass)
    c = TuyaScaleDataUpdateCoordinator(hass, api, interval, entry)
    c.sharing = object()  # push is configured
    c.resyncs = []
    c.resync = lambda: c.resyncs.append(1)
    return c


async def test_connecting_stops_the_polling_timer(hass):
    c = make(hass, FakeApi([[record(1000)]]))
    c.async_add_listener(lambda: None)  # entities listening: a refresh timer is pending
    await c.async_refresh()
    assert c._unsub_refresh is not None
    c.set_push_connected(True)
    assert c.update_interval is None
    assert c._unsub_refresh is None  # the pending timer does not fire once more


async def test_losing_the_connection_polls_again_at_the_capped_interval(hass):
    c = make(hass, FakeApi([[record(1000)]]), interval=86400)
    c.async_add_listener(lambda: None)
    await c.async_refresh()
    c.set_push_connected(True)
    assert c._unsub_refresh is None
    c.set_push_connected(False)
    assert c.update_interval == timedelta(seconds=3600)
    assert c._unsub_refresh is not None  # polling is scheduled again


async def test_first_connection_after_a_fresh_fetch_needs_no_extra_request(hass):
    c = make(hass, FakeApi([[record(1000)]]))
    await c.async_refresh()
    c.set_push_connected(True)
    assert c.resyncs == []


async def test_first_connection_with_stale_data_catches_up(hass, monkeypatch):
    c = make(hass, FakeApi([[record(1000)]]))
    await c.async_refresh()
    monkeypatch.setattr(coord_mod.time, "monotonic", lambda: c._last_fetch + 10_000)
    c.set_push_connected(True)  # the push link only came up long after the fetch
    assert c.resyncs == [1]


async def test_connection_that_never_fetched_catches_up(hass):
    c = make(hass, FakeApi([[record(1000)]]))
    c.set_push_connected(True)
    assert c.resyncs == [1]


async def test_reconnect_after_an_outage_always_catches_up(hass):
    c = make(hass, FakeApi([[record(1000)]]))
    await c.async_refresh()
    c.set_push_connected(True)
    assert c.resyncs == []
    c.set_push_connected(False)
    c.set_push_connected(True)  # even right after a fetch: the gap may hide a weighing
    assert c.resyncs == [1]


async def test_polling_only_setup_keeps_its_interval(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USERS: USERS}, options={})
    entry.add_to_hass(hass)
    c = TuyaScaleDataUpdateCoordinator(hass, FakeApi([[record(1)]]), 600, entry)
    c.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    assert c.update_interval == timedelta(seconds=600)
    c.set_push_connected(False)
    assert c.update_interval == timedelta(seconds=600)
