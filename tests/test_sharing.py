"""MQTT client: message filtering, health/restart logic, token persistence."""
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_body_fat_scale import sharing
from custom_components.tuya_body_fat_scale.const import (
    CONF_DEVICE_ID,
    CONF_SESSION,
    CONF_USER_CODE,
    DOMAIN,
)

DEV = "dev123"


def msg(dev=DEV, protocol=4, status=None):
    return {"protocol": protocol, "data": {"devId": dev, "status": status or []}}


def test_measurement_by_dp_id():
    assert sharing.is_measurement_message(msg(status=[{"dpId": 103, "value": 70500}]), DEV)


def test_measurement_by_code():
    assert sharing.is_measurement_message(msg(status=[{"code": "BR", "value": 500}]), DEV)


def test_battery_only_is_not_measurement():
    assert not sharing.is_measurement_message(msg(status=[{"dpId": 109, "value": True}]), DEV)


def test_other_device_and_protocol_ignored():
    st = [{"dpId": 103, "value": 1}]
    assert not sharing.is_measurement_message(msg(dev="other", status=st), DEV)
    assert not sharing.is_measurement_message(msg(protocol=20, status=st), DEV)
    assert not sharing.is_measurement_message({"protocol": 4}, DEV)


def test_session_token_drops_extra_keys():
    info = {"t": 1, "uid": "u", "expire_time": 7200, "access_token": "a",
            "refresh_token": "r", "endpoint": "https://x", "terminal_id": "t"}
    assert set(sharing.session_token(info)) == {"t", "uid", "expire_time", "access_token", "refresh_token"}


class FakeMQ:
    def __init__(self, alive=True, connected=True):
        self.alive, self.is_conn = alive, connected
        self.client = MagicMock()
        self.client.is_connected.side_effect = lambda: self.is_conn
        self.listeners = []
        self.stopped = False

    def is_alive(self):
        return self.alive

    def add_message_listener(self, fn):
        self.listeners.append(fn)

    def stop(self):
        self.stopped = True


class FakeDevice:
    set_up = False


class FakeManager:
    def __init__(self, devices=(DEV,)):
        self.device_map = {d: FakeDevice() for d in devices}
        self.mq = None
        self.updated = 0

    def update_device_cache(self):
        self.updated += 1

    def refresh_mq(self):
        self.mq = FakeMQ()


@pytest.fixture
def entry(hass):
    e = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_ID: DEV,
            CONF_USER_CODE: "uc",
            CONF_SESSION: {"endpoint": "https://apigw.tuyaeu.com", "terminal_id": "term",
                           "token": {"access_token": "old", "refresh_token": "r"}},
        },
    )
    e.add_to_hass(hass)
    return e


def make_client(hass, entry, managers, events):
    it = iter(managers)
    return sharing.ScaleSharingClient(
        hass, entry, lambda: events.append("m"), lambda c: events.append(c),
        manager_factory=lambda session, code, listener: next(it),
    )


async def test_connects_and_flags_connected(hass, entry):
    events, mgr = [], FakeManager()
    client = make_client(hass, entry, [mgr], events)
    await client.async_start()
    assert mgr.device_map[DEV].set_up is True
    assert mgr.mq.listeners
    await client._async_health_check()
    assert client.connected and events == [True]
    await client.async_stop()
    assert mgr.mq.stopped and events[-1] is False


async def test_only_scale_is_subscribed(hass, entry):
    mgr = FakeManager(devices=(DEV, "lamp"))
    client = make_client(hass, entry, [mgr], [])
    await client.async_start()
    assert mgr.device_map["lamp"].set_up is False
    await client.async_stop()


async def test_message_triggers_callback(hass, entry):
    events, mgr = [], FakeManager()
    client = make_client(hass, entry, [mgr], events)
    await client.async_start()
    mgr.mq.listeners[0](msg(status=[{"dpId": 103, "value": 70500}]))
    await hass.async_block_till_done()
    assert events == ["m"]
    await client.async_stop()


async def test_unhealthy_connection_is_rebuilt(hass, entry):
    events = []
    first, second = FakeManager(), FakeManager()
    client = make_client(hass, entry, [first, second], events)
    await client.async_start()
    await client._async_health_check()
    assert events == [True]
    first.mq.is_conn = False
    for _ in range(sharing.MQTT_RESTART_AFTER_CHECKS):
        await client._async_health_check()
    assert first.mq.stopped
    assert client._manager is second
    assert events[:2] == [True, False]
    await client._async_health_check()
    assert client.connected
    await client.async_stop()


async def test_dead_thread_counts_as_unhealthy(hass, entry):
    mgr = FakeManager()
    client = make_client(hass, entry, [mgr, FakeManager()], [])
    await client.async_start()
    mgr.mq.alive = False
    assert not sharing.mqtt_connected(mgr)
    await client.async_stop()


async def test_start_failure_falls_back_without_raising(hass, entry):
    events = []

    def boom(session, code, listener):
        raise RuntimeError("network down")

    client = sharing.ScaleSharingClient(hass, entry, lambda: None, events.append, manager_factory=boom)
    await client.async_start()
    assert not client.connected and client._manager is None
    await client._async_health_check()
    assert not client.connected
    await client.async_stop()


async def test_device_missing_from_account(hass, entry):
    client = make_client(hass, entry, [FakeManager(devices=("other",))], [])
    await client.async_start()
    assert client._manager is None and not client.connected
    await client.async_stop()


async def test_token_refresh_keeps_endpoint_and_terminal(hass, entry):
    client = make_client(hass, entry, [FakeManager()], [])
    await client.async_start()
    listener = sharing._TokenListener(client)
    listener.update_token({"access_token": "new", "refresh_token": "r2", "t": 5, "expire_time": 7200, "uid": "u"})
    await hass.async_block_till_done()
    s = entry.data[CONF_SESSION]
    assert s["token"]["access_token"] == "new"
    assert s["endpoint"] == "https://apigw.tuyaeu.com" and s["terminal_id"] == "term"
    await client.async_stop()


async def test_silent_reconnect_triggers_catch_up(hass, entry):
    resyncs = []
    mgr = FakeManager()
    client = sharing.ScaleSharingClient(
        hass, entry, lambda: None, lambda c: None,
        manager_factory=lambda s, c, listener: mgr, on_resync=lambda: resyncs.append(1),
    )
    await client.async_start()
    await client._async_health_check()  # first connect: nothing to catch up
    await client._async_health_check()  # same client object: still nothing
    assert resyncs == []
    # the SDK reconnected within one check interval: new paho client object
    new_client = MagicMock()
    new_client.is_connected.return_value = True
    mgr.mq.client = new_client
    await client._async_health_check()
    assert resyncs == [1]
    await client._async_health_check()
    assert resyncs == [1]
    await client.async_stop()


def test_old_sdk_version_warns(monkeypatch, caplog):
    monkeypatch.setattr(sharing.importlib.metadata, "version", lambda name: "0.2.8")
    assert sharing.sdk_version() == (0, 2, 8)
    sharing.warn_if_old_sdk()
    assert "older than 0.2.15" in caplog.text


def test_current_sdk_version_is_quiet(monkeypatch, caplog):
    monkeypatch.setattr(sharing.importlib.metadata, "version", lambda name: "0.2.15")
    sharing.warn_if_old_sdk()
    assert "older than" not in caplog.text
