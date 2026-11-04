"""Full setup/unload with a fake API and a fake MQTT manager."""
from datetime import timedelta
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_body_fat_scale import sharing
from custom_components.tuya_body_fat_scale.const import (
    CONF_SESSION,
    CONF_USER_CODE,
    CONF_USERS,
    DOMAIN,
    MQTT_FALLBACK_INTERVAL,
)

from .test_coordinator import USERS, FakeApi, record
from .test_sharing import DEV, FakeManager, msg


def make_entry(hass, with_session=True):
    data = {
        "access_id": "id", "access_key": "key", "device_id": DEV, "region": "eu",
        CONF_USERS: USERS,
    }
    if with_session:
        data[CONF_USER_CODE] = "uc"
        data[CONF_SESSION] = {"endpoint": "https://apigw.tuyaeu.com", "terminal_id": "term",
                              "token": {"access_token": "old", "refresh_token": "r"}}
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={"scan_interval": 300})
    entry.add_to_hass(hass)
    return entry


def patches(manager):
    def client(hass, entry, on_m, on_c, **kwargs):
        return sharing.ScaleSharingClient(
            hass, entry, on_m, on_c, manager_factory=lambda s, c, listener: manager,
            **kwargs,
        )

    api = FakeApi([[record(1000)]])
    return (
        patch("custom_components.tuya_body_fat_scale.TuyaScaleAPI", return_value=api),
        patch("custom_components.tuya_body_fat_scale.ScaleSharingClient", side_effect=client),
    )


async def test_setup_push_and_unload(hass):
    manager = FakeManager()
    entry = make_entry(hass)
    p_api, p_client = patches(manager)
    with p_api, p_client as client_cls:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.sharing is not None
        assert coordinator.update_interval == timedelta(seconds=300)

        await coordinator.sharing._async_health_check()
        assert coordinator.push_connected
        assert coordinator.update_interval == timedelta(seconds=MQTT_FALLBACK_INTERVAL)

        # a token refresh updates the entry but must not reload the integration
        sharing._TokenListener(coordinator.sharing).update_token(
            {"access_token": "new", "refresh_token": "r2", "t": 1, "expire_time": 7200, "uid": "u"})
        await hass.async_block_till_done()
        assert entry.data[CONF_SESSION]["token"]["access_token"] == "new"
        assert client_cls.call_count == 1
        assert entry.state is ConfigEntryState.LOADED

        # a push message for the scale schedules a refresh
        manager.mq.listeners[0](msg(status=[{"dpId": 103, "value": 70500}]))
        await hass.async_block_till_done()
        assert coordinator._trigger_task is not None
        coordinator._trigger_task.cancel()

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert manager.mq.stopped


async def test_options_change_reloads(hass):
    entry = make_entry(hass, with_session=False)
    p_api, p_client = patches(FakeManager())
    with p_api as api_cls, p_client:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert api_cls.call_count == 1
        hass.config_entries.async_update_entry(entry, options={"scan_interval": 600})
        await hass.async_block_till_done()
        assert api_cls.call_count == 2


async def test_without_session_stays_polling(hass):
    entry = make_entry(hass, with_session=False)
    p_api, p_client = patches(FakeManager())
    with p_api, p_client as client_cls:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert client_cls.call_count == 0
        assert hass.data[DOMAIN][entry.entry_id].sharing is None
        assert await hass.config_entries.async_unload(entry.entry_id)


# -- diagnostics entities, weighing event, diagnostics download ---------------------------

from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402

from custom_components.tuya_body_fat_scale.diagnostics import async_get_config_entry_diagnostics  # noqa: E402


async def test_push_adds_no_extra_entities_or_devices(hass):
    manager = FakeManager()
    entry = make_entry(hass)
    p_api, p_client = patches(manager)
    with p_api, p_client:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.sharing._async_health_check()
        manager.mq.listeners[0](msg(status=[{"dpId": 109, "value": True}]))
        await hass.async_block_till_done()
        assert coordinator.push_connected
        assert coordinator.last_push_message is not None  # kept for the diagnostics download

        registered = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        assert {e.domain for e in registered} == {"sensor", "button"}
        assert not any(e.unique_id.endswith(("_push_connected", "_last_push_message", "_weighed")) for e in registered)
        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        assert {next(iter(d.identifiers))[1] for d in devices} == {f"{entry.entry_id}_u1"}
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_leftovers_of_beta4_are_removed(hass):
    entry = make_entry(hass, with_session=False)
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    stale = [
        ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{entry.entry_id}_push_connected", config_entry=entry),
        ent_reg.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_last_push_message", config_entry=entry),
        ent_reg.async_get_or_create("event", DOMAIN, f"{entry.entry_id}_u1_weighed", config_entry=entry),
    ]
    dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)})
    p_api, p_client = patches(FakeManager())
    with p_api, p_client:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    for item in stale:
        assert ent_reg.async_get(item.entity_id) is None
    assert dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)}) is None
    # the real user entities are untouched
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_u1_weight") is not None


async def test_diagnostics_hide_secrets(hass):
    manager = FakeManager()
    entry = make_entry(hass)
    p_api, p_client = patches(manager)
    with p_api, p_client:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.data[DOMAIN][entry.entry_id].sharing._async_health_check()
        info = await async_get_config_entry_diagnostics(hass, entry)
        text = str(info)
        for secret in ("'access_id': 'id'", "'key'", "'uc'", "apigw", "'term'", "'old'", "dev123"):
            assert secret not in text, secret
        assert info["has_qr_session"] is True
        assert info["users"] == 1
        assert info["coordinator"]["push_connected"] is True
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_removing_the_entry_drops_repairs_issues(hass):
    from homeassistant.helpers import issue_registry as ir

    entry = make_entry(hass, with_session=False)
    ir.async_create_issue(
        hass, DOMAIN, f"api_quota_exhausted_{entry.entry_id}", is_fixable=False,
        severity=ir.IssueSeverity.WARNING, translation_key="api_quota_exhausted",
        translation_placeholders={"error": "x"},
    )
    p_api, p_client = patches(FakeManager())
    with p_api, p_client:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"api_quota_exhausted_{entry.entry_id}") is None
