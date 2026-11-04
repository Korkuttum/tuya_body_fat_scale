"""Regressions found in the full code review."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_body_fat_scale import coordinator as coord_mod
from custom_components.tuya_body_fat_scale import sharing
from custom_components.tuya_body_fat_scale.api import TuyaScaleAPI
from custom_components.tuya_body_fat_scale.const import CONF_USERS, DOMAIN
from custom_components.tuya_body_fat_scale.coordinator import TuyaScaleDataUpdateCoordinator

from .test_coordinator import FakeApi, record
from .test_sharing import FakeManager, entry, make_client  # noqa: F401  (entry is a fixture)

TWO = {
    "u1": {"birth_date": "01.01.1990", "gender": "M", "name": "Ali"},
    "u2": {"birth_date": "02.02.1992", "gender": "F", "name": "Ayse"},
}


def other_user(ts, user_id="u2", name="Ayse"):
    return {**record(ts), "user_id": user_id, "nick_name": name}


def make(hass, api, users=TWO, options=None):
    cfg = MockConfigEntry(domain=DOMAIN, data={CONF_USERS: users}, options=options or {})
    cfg.add_to_hass(hass)
    return TuyaScaleDataUpdateCoordinator(hass, api, 300, cfg)


# -- MQTT client ---------------------------------------------------------------------------


async def test_unload_while_connecting_leaves_nothing_running(hass, entry):  # noqa: F811
    manager = FakeManager()
    holder = {}

    def factory(session, code, listener):
        holder["client"]._stopped = True  # async_stop ran while the executor job was busy
        return manager

    client = sharing.ScaleSharingClient(hass, entry, lambda: None, lambda c: None, manager_factory=factory)
    holder["client"] = client
    await client.async_start()
    assert client._manager is None
    assert manager.mq.stopped  # the connection that finished late was shut down again
    await client._async_health_check()  # and nothing is rebuilt afterwards
    assert client._manager is None
    await client.async_stop()


async def test_failure_while_subscribing_stops_the_mqtt_thread(hass, entry):  # noqa: F811
    manager = FakeManager()
    refresh = manager.refresh_mq

    def refresh_then_break():
        refresh()
        manager.mq.add_message_listener = MagicMock(side_effect=RuntimeError("boom"))

    manager.refresh_mq = refresh_then_break
    client = make_client(hass, entry, [manager], [])
    await client.async_start()
    assert manager.mq.stopped
    assert client._manager is None
    await client.async_stop()


# -- coordinator ---------------------------------------------------------------------------


async def test_user_missing_from_a_fetch_keeps_the_last_values(hass):
    api = FakeApi([[record(1000), other_user(1100)], [record(2000)]])
    c = make(hass, api)
    await c.async_refresh()
    assert set(c.data) == {"u1", "u2"}
    await c.async_refresh()  # a short page that has only u1's newest record
    assert set(c.data) == {"u1", "u2"}
    assert c.data["u2"]["last_measurement"]  # u2 is still there with its old values


async def test_weighing_of_an_unconfigured_user_ends_the_retries(hass, monkeypatch):
    monkeypatch.setattr(coord_mod, "MQTT_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(coord_mod, "MQTT_RETRY_DELAY", 0)
    stranger = {**record(2000), "user_id": "0", "nick_name": "unknown"}
    api = FakeApi([[record(1000)], [stranger, record(1000)]])
    c = make(hass, api, users={"u1": TWO["u1"]})
    await c.async_refresh()
    c.handle_measurement()
    await c._trigger_task
    assert api.calls == 2  # baseline + one refresh, not four


async def test_interval_change_reschedules_the_pending_refresh(hass):
    c = make(hass, FakeApi([[record(1)]]))
    c.async_add_listener(lambda: None)
    c.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    with patch.object(c, "_schedule_refresh") as schedule:
        c.set_push_connected(True)
        schedule.assert_called_once()


async def test_reconnect_catch_up_reads_only_the_newest_page(hass):
    api = FakeApi([[record(1000)]])
    c = make(hass, api, users={"u1": TWO["u1"]})
    await c.async_refresh()
    await c._async_refresh_recent()
    assert api.pages == [5, 1]
    assert c._max_pages == 5


# -- setup ---------------------------------------------------------------------------------


async def test_rejected_api_credentials_start_reauthentication(hass):
    cfg = MockConfigEntry(
        domain=DOMAIN,
        data={"access_id": "id", "access_key": "key", "device_id": "dev123", "region": "eu", CONF_USERS: TWO},
        options={"scan_interval": 300, "api_error_notification": False},
    )
    cfg.add_to_hass(hass)
    api = FakeApi([[record(1)]])
    api.error = Exception("API error: token invalid")
    with patch("custom_components.tuya_body_fat_scale.TuyaScaleAPI", return_value=api):
        assert not await hass.config_entries.async_setup(cfg.entry_id)
        await hass.async_block_till_done()
    assert cfg.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_last_measurement_is_converted_from_host_local_time(hass):
    from .test_init import make_entry, patches

    cfg = make_entry(hass, with_session=False)
    p_api, p_client = patches(FakeManager())
    with p_api, p_client:
        assert await hass.config_entries.async_setup(cfg.entry_id)
        await hass.async_block_till_done()
        state = hass.states.get("sensor.tuya_body_fat_scale_ali_last_measurement")
    # FakeApi reports the wall-clock time "2026-01-01 10:00:00" of the host
    expected = datetime(2026, 1, 1, 10, 0, 0).astimezone(timezone.utc)
    assert datetime.fromisoformat(state.state) == expected


# -- Tuya API client -----------------------------------------------------------------------


def response(body, status=200):
    return SimpleNamespace(status_code=status, text=str(body), json=lambda: body)


@pytest.fixture
def api(hass):
    return TuyaScaleAPI(hass, {"access_id": "MY-ACCESS-ID", "access_key": "MY-ACCESS-KEY",
                               "device_id": "dev", "region": "eu"})


TOKEN_OK = {"success": True, "result": {"access_token": "SECRET-TOKEN", "expire_time": 7200}}


async def test_a_persistently_rejected_token_is_retried_only_once(api):
    calls = []

    def fake(url, headers, data=""):
        calls.append(url)
        if "token?grant_type" in url:
            return response(TOKEN_OK)
        return response({"success": False, "msg": "token invalid"})

    with patch("custom_components.tuya_body_fat_scale.api.make_api_request", side_effect=fake):
        with pytest.raises(Exception, match="token invalid"):
            await api._api_request("GET", "/v1.0/scales/dev/datas/history")
    assert len(calls) == 4  # token, request, fresh token, request: then it gives up


async def test_http_401_is_retried_only_once(api):
    calls = []

    def fake(url, headers, data=""):
        calls.append(url)
        if "token?grant_type" in url:
            return response(TOKEN_OK)
        return response({}, status=401)

    with patch("custom_components.tuya_body_fat_scale.api.make_api_request", side_effect=fake):
        with pytest.raises(Exception, match="401"):
            await api._api_request("GET", "/v1.0/scales/dev/datas/history")
    assert len(calls) == 4


async def test_a_token_that_is_refused_once_recovers(api):
    state = {"data_calls": 0}

    def fake(url, headers, data=""):
        if "token?grant_type" in url:
            return response(TOKEN_OK)
        state["data_calls"] += 1
        if state["data_calls"] == 1:
            return response({"success": False, "msg": "token invalid"})
        return response({"success": True, "result": {"records": []}})

    with patch("custom_components.tuya_body_fat_scale.api.make_api_request", side_effect=fake):
        assert await api._api_request("GET", "/x") == {"records": []}


async def test_timeout_is_reported_as_such(api):
    with patch("custom_components.tuya_body_fat_scale.api.make_api_request", side_effect=requests.exceptions.Timeout):
        with pytest.raises(Exception, match="timed out"):
            await api._api_request("GET", "/x")


async def test_debug_logs_contain_no_secrets(api, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="custom_components.tuya_body_fat_scale")

    def fake(url, headers, data=""):
        if "token?grant_type" in url:
            return response(TOKEN_OK)
        return response({"success": True, "result": {"records": []}})

    with patch("custom_components.tuya_body_fat_scale.api.make_api_request", side_effect=fake):
        await api._api_request("GET", "/x")
    for secret in ("SECRET-TOKEN", "MY-ACCESS-KEY", "MY-ACCESS-ID"):
        assert secret not in caplog.text, secret
