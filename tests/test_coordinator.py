"""Coordinator: analysis cache, push-triggered refresh with retries, interval switching."""
from datetime import timedelta
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_body_fat_scale import coordinator as coord_mod
from custom_components.tuya_body_fat_scale.const import CONF_USERS, DOMAIN, MQTT_FALLBACK_INTERVAL
from custom_components.tuya_body_fat_scale.coordinator import TuyaScaleDataUpdateCoordinator

USERS = {"u1": {"birth_date": "01.01.1990", "gender": "M", "name": "Ali"}}


def record(ts, weight=70.5):
    return {"user_id": "u1", "nick_name": "Ali", "create_time": ts, "weight": weight,
            "height": 175, "resistance": "0.5"}


class FakeApi:
    def __init__(self, records_seq):
        self.seq = list(records_seq)
        self.calls = 0
        self.pages = []
        self.reports = 0

    async def get_scale_records(self, max_pages=5):
        self.pages.append(max_pages)
        idx = min(self.calls, len(self.seq) - 1)
        self.calls += 1
        return {"records": self.seq[idx]}

    async def get_analysis_report(self, data):
        self.reports += 1
        return {"fat": 20.0, "body_type": 1}

    @staticmethod
    def format_datetime(ts):
        return "2026-01-01 10:00:00"


def make(hass, api):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USERS: USERS}, options={})
    entry.add_to_hass(hass)
    return TuyaScaleDataUpdateCoordinator(hass, api, 300, entry)


async def test_analysis_is_cached_per_measurement(hass):
    api = FakeApi([[record(1000)], [record(1000)], [record(2000)]])
    c = make(hass, api)
    await c.async_refresh()
    await c.async_refresh()
    assert api.reports == 1  # same measurement -> no second analysis call
    await c.async_refresh()
    assert api.reports == 2  # new measurement -> new analysis
    assert c.data["u1"]["body_fat"] == 20.0


async def test_interval_switches_with_push_state(hass):
    c = make(hass, FakeApi([[record(1)]]))
    c.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    c.set_push_connected(True)
    assert c.update_interval == timedelta(seconds=MQTT_FALLBACK_INTERVAL)
    c.set_push_connected(False)
    assert c.update_interval == timedelta(seconds=300)


async def test_push_refresh_retries_until_new_record(hass, monkeypatch):
    monkeypatch.setattr(coord_mod, "MQTT_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(coord_mod, "MQTT_RETRY_DELAY", 0)
    # first refresh = baseline; push -> two stale answers, then the new record
    api = FakeApi([[record(1000)], [record(1000)], [record(1000)], [record(2000)]])
    c = make(hass, api)
    await c.async_refresh()
    c.handle_measurement()
    await c._trigger_task
    assert api.calls == 4
    assert c._latest_ts == 2000
    assert api.pages[1:] == [1, 1, 1]  # push refreshes fetch only the first page
    assert c._max_pages == 5


async def test_push_refresh_gives_up_after_retries(hass, monkeypatch):
    monkeypatch.setattr(coord_mod, "MQTT_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(coord_mod, "MQTT_RETRY_DELAY", 0)
    api = FakeApi([[record(1000)]])
    c = make(hass, api)
    await c.async_refresh()
    c.handle_measurement()
    await c._trigger_task
    assert api.calls == 1 + coord_mod.MQTT_RETRIES + 1


async def test_new_message_restarts_debounce(hass, monkeypatch):
    monkeypatch.setattr(coord_mod, "MQTT_DEBOUNCE_SECONDS", 0.2)
    monkeypatch.setattr(coord_mod, "MQTT_RETRY_DELAY", 0)
    api = FakeApi([[record(1000)], [record(2000)]])
    c = make(hass, api)
    await c.async_refresh()
    c.handle_measurement()
    first = c._trigger_task
    c.handle_measurement()
    await c._trigger_task
    assert first.cancelled()
    assert api.calls == 2  # one baseline + a single push refresh


async def test_null_height_record_is_skipped(hass):
    bad = record(2000)
    bad["height"] = "null"
    api = FakeApi([[record(1000), bad]])
    c = make(hass, api)
    await c.async_refresh()
    # the newest record is unusable -> fall back to the last valid one, no analysis crash
    assert c.data["u1"]["weight"] == 70.5
    assert api.reports == 1
    assert c.last_update_success


async def test_fallback_interval_is_capped_when_push_is_configured(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USERS: USERS}, options={})
    entry.add_to_hass(hass)
    c = TuyaScaleDataUpdateCoordinator(hass, FakeApi([[record(1)]]), 86400, entry)
    c.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    c.set_push_connected(False)
    assert c.update_interval == timedelta(seconds=86400)  # polling-only keeps the user's value
    c.sharing = object()
    c.set_push_connected(False)
    assert c.update_interval == timedelta(seconds=3600)
    c.set_push_connected(True)
    assert c.update_interval == timedelta(seconds=MQTT_FALLBACK_INTERVAL)
