"""Config flow: device picker, user selection, height, re-authentication and the options menu."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_body_fat_scale import config_flow as cf
from custom_components.tuya_body_fat_scale.const import CONF_SESSION, CONF_USER_CODE, DOMAIN

from .test_config_flow import BASE, INFO, api_patch, login_patch, setup_patch, start

MANY = {"records": [
    {"user_id": "0", "nick_name": "unknown"},
    {"user_id": "u1", "nick_name": "Ali"},
    {"user_id": "u2", "nick_name": "Ayse"},
]}

TWO_USERS = {
    "u1": {"birth_date": "01.01.1990", "gender": "M", "name": "Ali"},
    "u2": {"birth_date": "02.02.1992", "gender": "F", "name": "Ayse"},
}


def api_with(records):
    api = MagicMock()
    api.get_scale_records = AsyncMock(return_value=records)
    return patch.object(cf, "TuyaScaleAPI", return_value=api)


def default_of(result, key):
    marker = next(k for k in result["data_schema"].schema if k == key)
    return marker.default()


# -- setup: device picker, user selection, height ---------------------------------------------


async def test_device_id_or_user_code_is_required(hass):
    result = await start(hass, {**BASE, "device_id": ""})
    assert result["step_id"] == "user" and result["errors"] == {"base": "device_id_required"}


async def test_pick_the_scale_from_the_account(hass):
    lp, _ = login_patch([(True, INFO)])
    manager = MagicMock(device_map={
        "lamp": SimpleNamespace(name="Lamp", category="dj"),
        "scaleX": SimpleNamespace(name="Bathroom scale", category="tzc1"),
    })
    with api_patch(), lp, setup_patch(), patch.object(cf, "build_manager", return_value=manager):
        result = await start(hass, {**BASE, "device_id": "", CONF_USER_CODE: "123"})
        assert result["step_id"] == "qr"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "device"
        options = result["data_schema"].schema["device_id"].config["options"]
        assert [o["value"] for o in options] == ["scaleX", "lamp"]  # scales first
        assert default_of(result, "device_id") == "scaleX"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device_id": "scaleX"})
        assert result["step_id"] == "users"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"birth_date": "01.01.1990", "gender": "M", "height": 178})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["device_id"] == "scaleX"
    assert result["data"]["users"]["u1"]["height"] == 178


async def test_account_without_devices_aborts(hass):
    lp, _ = login_patch([(True, INFO)])
    with api_patch(), lp, patch.object(cf, "build_manager", return_value=MagicMock(device_map={})):
        result = await start(hass, {**BASE, "device_id": "", CONF_USER_CODE: "123"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.ABORT and result["reason"] == "no_devices"


async def test_unassigned_user_is_not_preselected(hass):
    with api_with(MANY), setup_patch():
        result = await start(hass, BASE)
        assert result["step_id"] == "select_users"
        assert default_of(result, "selected") == ["u1", "u2"]
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"selected": []})
        assert result["errors"] == {"base": "no_users_selected"}
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"selected": ["u1", "u2"]})
        assert result["step_id"] == "users" and result["description_placeholders"]["user_name"] == "Ali"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"birth_date": "01.01.1990", "gender": "M"})
        assert result["description_placeholders"]["user_name"] == "Ayse"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"birth_date": "02.02.1992", "gender": "F", "height": 165})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert set(result["data"]["users"]) == {"u1", "u2"}  # the unassigned "0" was left out
    assert "height" not in result["data"]["users"]["u1"]
    assert result["data"]["users"]["u2"]["height"] == 165


async def test_single_user_skips_the_selection(hass):
    with api_patch(), setup_patch():
        result = await start(hass, BASE)
    assert result["step_id"] == "users"


async def test_invalid_birth_date_is_rejected(hass):
    with api_patch(), setup_patch():
        result = await start(hass, BASE)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"birth_date": "1990-01-01", "gender": "M"})
    assert result["errors"] == {"birth_date": "invalid_date"}


# -- re-authentication -----------------------------------------------------------------------


def make_entry(hass, users=None, with_session=True):
    data = {
        **{k: BASE[k] for k in ("access_id", "access_key", "device_id", "region")},
        "users": users or {"u1": {"birth_date": "01.01.1990", "gender": "M", "name": "Ali"}},
    }
    if with_session:
        data[CONF_USER_CODE] = "123"
        data[CONF_SESSION] = {"endpoint": "https://old", "terminal_id": "t", "token": {"access_token": "dead"}}
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={"scan_interval": 300})
    entry.add_to_hass(hass)
    return entry


async def start_reauth(hass, entry, target=None):
    context = {"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id}
    if target:
        context["reauth_target"] = target
    return await hass.config_entries.flow.async_init(DOMAIN, context=context, data=dict(entry.data))


async def test_reauth_of_the_qr_session(hass):
    entry = make_entry(hass)
    lp, _ = login_patch([(True, INFO)])
    manager = MagicMock(device_map={"dev1": object()})
    with lp, setup_patch(), patch.object(cf, "build_manager", return_value=manager):
        result = await start_reauth(hass, entry, "session")
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_USER_CODE: " 456 "})
        assert result["step_id"] == "qr"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_USER_CODE] == "456"
    assert entry.data[CONF_SESSION]["token"]["access_token"] == "a"
    assert entry.data[CONF_SESSION]["endpoint"] == "https://apigw.tuyaeu.com"
    assert entry.data["access_key"] == "key"  # everything else is kept


async def test_reauth_session_wrong_account_stays_on_qr(hass):
    entry = make_entry(hass)
    lp, _ = login_patch([(True, INFO)])
    with lp, patch.object(cf, "build_manager", return_value=MagicMock(device_map={"other": object()})):
        result = await start_reauth(hass, entry, "session")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_USER_CODE: "456"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "qr" and result["errors"] == {"base": "device_not_in_account"}
    assert entry.data[CONF_SESSION]["token"]["access_token"] == "dead"


async def test_reauth_of_the_api_credentials(hass):
    entry = make_entry(hass, with_session=False)
    with api_patch(), setup_patch():
        result = await start_reauth(hass, entry)  # raised by HA itself, no target
        assert result["step_id"] == "reauth_api"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"access_id": "new-id", "access_key": "new-key"})
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data["access_id"] == "new-id" and entry.data["access_key"] == "new-key"


async def test_reauth_api_rejects_bad_credentials(hass):
    entry = make_entry(hass, with_session=False)
    api = MagicMock()
    api.get_scale_records = AsyncMock(side_effect=Exception("invalid token"))
    with patch.object(cf, "TuyaScaleAPI", return_value=api):
        result = await start_reauth(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"access_id": "x", "access_key": "y"})
    assert result["step_id"] == "reauth_api" and result["errors"] == {"base": "invalid_auth"}
    assert entry.data["access_key"] == "key"


# -- options -----------------------------------------------------------------------------------


async def open_options(hass, entry, step):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert set(result["menu_options"]) == {"settings", "relink", "edit_users", "add_users"}
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})


async def test_options_settings(hass):
    entry = make_entry(hass)
    result = await open_options(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 900, "api_error_notification": False})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 900, "api_error_notification": False}


async def test_options_relink_qr(hass):
    entry = make_entry(hass)
    lp, _ = login_patch([(False, {}), (True, INFO)])
    manager = MagicMock(device_map={"dev1": object()})
    with lp, setup_patch(), patch.object(cf, "build_manager", return_value=manager):
        result = await open_options(hass, entry, "relink")
        assert default_of(result, CONF_USER_CODE) == "123"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_USER_CODE: "789"})
        assert result["step_id"] == "relink_qr"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        assert result["errors"] == {"base": "qr_not_scanned"}
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_USER_CODE] == "789"
    assert entry.data[CONF_SESSION]["token"]["access_token"] == "a"
    assert entry.options == {"scan_interval": 300}  # untouched


async def test_options_relink_with_a_rejected_code(hass):
    entry = make_entry(hass, with_session=False)
    lc = MagicMock()
    lc.qr_code.return_value = {"success": False, "msg": "bad"}
    with patch.object(cf, "LoginControl", return_value=lc):
        result = await open_options(hass, entry, "relink")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_USER_CODE: "bad"})
    assert result["step_id"] == "relink" and result["errors"] == {"base": "qr_failed"}


async def test_options_edit_user(hass):
    entry = make_entry(hass)
    with setup_patch():
        result = await open_options(hass, entry, "edit_users")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"user_id": "u1"})
        assert result["step_id"] == "edit_user"
        assert result["description_placeholders"]["user_name"] == "Ali"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"birth_date": "bad", "gender": "M"})
        assert result["errors"] == {"birth_date": "invalid_date"}
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"birth_date": "02.02.1991", "gender": "F", "height": 181})
        await hass.async_block_till_done()
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert entry.data["users"]["u1"] == {
            "birth_date": "02.02.1991", "gender": "F", "name": "Ali", "height": 181}

        # clearing the height removes it again
        result = await open_options(hass, entry, "edit_users")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"user_id": "u1"})
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"birth_date": "02.02.1991", "gender": "F"})
        await hass.async_block_till_done()
    assert "height" not in entry.data["users"]["u1"]


async def test_options_remove_user_cleans_the_registries(hass):
    entry = make_entry(hass, users=TWO_USERS)
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    for uid in ("u1", "u2"):
        ent_reg.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_{uid}_weight", config_entry=entry)
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{entry.entry_id}_{uid}")})
    with setup_patch():
        result = await open_options(hass, entry, "edit_users")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"user_id": "u2"})
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"birth_date": "02.02.1992", "gender": "F", "remove": True})
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert set(entry.data["users"]) == {"u1"}
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_u2_weight") is None
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_u1_weight") is not None
    assert dev_reg.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_u2")}) is None
    assert dev_reg.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_u1")}) is not None


async def test_options_cannot_remove_the_last_user(hass):
    entry = make_entry(hass)
    result = await open_options(hass, entry, "edit_users")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"user_id": "u1"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"birth_date": "01.01.1990", "gender": "M", "remove": True})
    assert result["errors"] == {"base": "cannot_remove_last"}
    assert set(entry.data["users"]) == {"u1"}


async def test_options_add_users(hass):
    entry = make_entry(hass)
    with api_with(MANY), setup_patch():
        result = await open_options(hass, entry, "add_users")
        assert result["step_id"] == "add_users"
        # u1 is already set up; the unassigned user is offered but not preselected
        assert [o["value"] for o in result["data_schema"].schema["selected"].config["options"]] == ["0", "u2"]
        assert default_of(result, "selected") == ["u2"]
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"selected": ["u2"]})
        assert result["step_id"] == "add_user"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"birth_date": "02.02.1992", "gender": "F", "height": 165})
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert set(entry.data["users"]) == {"u1", "u2"}
    assert entry.data["users"]["u2"]["height"] == 165


async def test_options_add_users_nothing_new(hass):
    entry = make_entry(hass)
    with api_with({"records": [{"user_id": "u1", "nick_name": "Ali"}]}):
        result = await open_options(hass, entry, "add_users")
    assert result["type"] == FlowResultType.ABORT and result["reason"] == "no_new_users"


async def test_options_add_users_api_failure(hass):
    entry = make_entry(hass)
    api = MagicMock()
    api.get_scale_records = AsyncMock(side_effect=Exception("down"))
    with patch.object(cf, "TuyaScaleAPI", return_value=api):
        result = await open_options(hass, entry, "add_users")
    assert result["type"] == FlowResultType.ABORT and result["reason"] == "cannot_connect"
