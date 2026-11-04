"""Config flow: polling-only path and the QR path."""
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tuya_body_fat_scale import config_flow as cf
from custom_components.tuya_body_fat_scale.const import CONF_SESSION, CONF_USER_CODE, DOMAIN

BASE = {"access_id": "id", "access_key": "key", "device_id": "dev1", "region": "eu",
        "scan_interval": 300, "api_error_notification": True}
RECORDS = {"records": [{"user_id": "u1", "nick_name": "Ali"}]}
INFO = {"t": 1, "uid": "eu1", "expire_time": 7200, "access_token": "a", "refresh_token": "r",
        "endpoint": "https://apigw.tuyaeu.com", "terminal_id": "term"}


def setup_patch():
    return patch("custom_components.tuya_body_fat_scale.async_setup_entry", return_value=True)


def api_patch():
    api = MagicMock()
    api.get_scale_records = AsyncMock(return_value=RECORDS)
    return patch.object(cf, "TuyaScaleAPI", return_value=api)


def login_patch(results):
    lc = MagicMock()
    lc.qr_code.return_value = {"success": True, "result": {"qrcode": "tok"}}
    lc.login_result.side_effect = results
    return patch.object(cf, "LoginControl", return_value=lc), lc


async def start(hass, data):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def test_region_is_a_dropdown(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    schema = result["data_schema"].schema
    region = next(v for k, v in schema.items() if k == "region")
    assert region.config["mode"] == "dropdown"
    assert [o["value"] for o in region.config["options"]] == ["eu", "us", "cn", "in"]


async def test_polling_only_skips_qr(hass):
    with api_patch(), setup_patch():
        result = await start(hass, BASE)
        assert result["step_id"] == "users"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"birth_date": "01.01.1990", "gender": "M"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_SESSION not in result["data"]


async def test_qr_path_creates_session(hass):
    lp, lc = login_patch([(False, {}), (True, INFO)])
    manager = MagicMock(device_map={"dev1": object()})
    with api_patch(), lp, setup_patch(), patch.object(cf, "build_manager", return_value=manager):
        result = await start(hass, {**BASE, CONF_USER_CODE: " 123 "})
        assert result["step_id"] == "qr"
        lc.qr_code.assert_called_once()
        # not scanned yet -> same step with an error
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "qr" and result["errors"] == {"base": "qr_not_scanned"}
        # scanned
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "users"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"birth_date": "01.01.1990", "gender": "M"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_USER_CODE] == "123"
    assert data[CONF_SESSION]["endpoint"] == "https://apigw.tuyaeu.com"
    assert data[CONF_SESSION]["terminal_id"] == "term"
    assert set(data[CONF_SESSION]["token"]) == {"t", "uid", "expire_time", "access_token", "refresh_token"}


async def test_device_not_in_qr_account(hass):
    lp, _ = login_patch([(True, INFO)])
    manager = MagicMock(device_map={"other": object()})
    with api_patch(), lp, patch.object(cf, "build_manager", return_value=manager):
        result = await start(hass, {**BASE, CONF_USER_CODE: "123"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "qr" and result["errors"] == {"base": "device_not_in_account"}


async def test_bad_user_code_shows_error(hass):
    lc = MagicMock()
    lc.qr_code.return_value = {"success": False, "msg": "bad code"}
    with api_patch(), patch.object(cf, "LoginControl", return_value=lc):
        result = await start(hass, {**BASE, CONF_USER_CODE: "nope"})
    assert result["step_id"] == "user" and result["errors"] == {"base": "qr_failed"}
