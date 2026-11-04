"""Config flow for Tuya Body Fat Scale integration."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import voluptuous as vol
from tuya_sharing import LoginControl

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .api import TuyaScaleAPI
from .const import (
    CONF_ACCESS_ID,
    CONF_ACCESS_KEY,
    CONF_API_ERROR_NOTIFICATION,
    CONF_BIRTH_DATE,
    CONF_DEVICE_ID,
    CONF_GENDER,
    CONF_HEIGHT,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_SESSION,
    CONF_USER_CODE,
    CONF_USER_ID,
    CONF_USERS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GENDER_OPTIONS,
    REAUTH_SESSION,
    REAUTH_TARGET,
    REGIONS,
    SHARING_CLIENT_ID,
    SHARING_SCHEMA,
)
from .helpers import find_device
from .sharing import build_manager, session_token

_LOGGER = logging.getLogger(__name__)

REGION_LABELS = {
    "eu": "Europe (eu)",
    "us": "Americas (us)",
    "cn": "China (cn)",
    "in": "India (in)",
}
REGION_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[{"value": r, "label": REGION_LABELS.get(r, r)} for r in REGIONS],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)

# Tuya product categories of body fat scales; only used to list them first.
SCALE_CATEGORIES = frozenset({"tzc1", "tzc2"})
# nick names the cloud gives to weighings it could not assign to a profile
UNKNOWN_NICK_NAMES = frozenset({"", "unknown", "null", "none"})

CONF_SELECTED = "selected"
CONF_REMOVE = "remove"
HEIGHT_VALIDATOR = vol.All(vol.Coerce(int), vol.Range(min=50, max=250))


# -- shared helpers ----------------------------------------------------------


def is_unassigned_user(user: dict[str, str]) -> bool:
    """Weighings the scale could not match to a profile (id 0 / name unknown)."""
    return str(user["id"]) == "0" or str(user["name"]).strip().lower() in UNKNOWN_NICK_NAMES


def api_error_key(err: Exception) -> str:
    return "invalid_auth" if "token" in str(err).lower() else "cannot_connect"


def users_from_records(records: dict[str, Any]) -> list[dict[str, str]]:
    """Distinct users (id + nick name) found in the scale history."""
    seen: set[str] = set()
    users: list[dict[str, str]] = []
    for record in records.get("records", []):
        user_id = record.get("user_id")
        nick_name = record.get("nick_name")
        if user_id and nick_name and user_id not in seen:
            seen.add(user_id)
            users.append({"id": user_id, "name": nick_name})
    return users


def user_selector(users: list[dict[str, str]], *, multiple: bool) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[{"value": u["id"], "label": f"{u['name']} ({u['id']})"} for u in users],
            multiple=multiple,
            mode=selector.SelectSelectorMode.LIST if multiple else selector.SelectSelectorMode.DROPDOWN,
        )
    )


def validate_user_input(user_input: dict[str, Any]) -> dict[str, str]:
    """Check the birth date of a user form."""
    try:
        birth_date = datetime.strptime(user_input[CONF_BIRTH_DATE], "%d.%m.%Y")
    except ValueError:
        return {CONF_BIRTH_DATE: "invalid_date"}
    today = datetime.now()
    age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    if not 0 <= age <= 120:
        return {CONF_BIRTH_DATE: "invalid_age"}
    return {}


def build_user(user_input: dict[str, Any], name: str, base: dict[str, Any] | None = None) -> dict[str, Any]:
    user = dict(base or {})
    user.update(
        {"birth_date": user_input[CONF_BIRTH_DATE], "gender": user_input[CONF_GENDER], "name": name}
    )
    if user_input.get(CONF_HEIGHT):
        user[CONF_HEIGHT] = int(user_input[CONF_HEIGHT])
    else:
        user.pop(CONF_HEIGHT, None)
    return user


def user_schema(defaults: dict[str, Any] | None = None, *, with_remove: bool = False) -> vol.Schema:
    """Birth date, gender and optional height (+ a remove switch when editing)."""
    d = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required(CONF_BIRTH_DATE, description={"suggested_value": d.get(CONF_BIRTH_DATE)}): str,
        vol.Required(CONF_GENDER, **({"default": d[CONF_GENDER]} if d.get(CONF_GENDER) else {})): vol.In(
            GENDER_OPTIONS
        ),
        vol.Optional(CONF_HEIGHT, description={"suggested_value": d.get(CONF_HEIGHT)}): HEIGHT_VALIDATOR,
    }
    if with_remove:
        fields[vol.Optional(CONF_REMOVE, default=False)] = bool
    return vol.Schema(fields)


class QrLogin:
    """QR login of the Tuya Smart / Smart Life account (shared by setup, reauth and options)."""

    def __init__(self, hass) -> None:
        self.hass = hass
        self._login = LoginControl()
        self._token: str | None = None

    async def new_qr(self, user_code: str) -> bool:
        """Ask Tuya for a login QR for the user code."""
        try:
            resp = await self.hass.async_add_executor_job(
                self._login.qr_code, SHARING_CLIENT_ID, SHARING_SCHEMA, user_code
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("QR request failed: %s", err)
            return False
        if not resp.get("success"):
            _LOGGER.error("QR request rejected: %s", resp.get("msg"))
            return False
        self._token = resp["result"]["qrcode"]
        return True

    def schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Optional("qr_code"): selector.QrCodeSelector(
                    config=selector.QrCodeSelectorConfig(
                        data=f"tuyaSmart--qrLogin?token={self._token}",
                        scale=5,
                        error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                    )
                )
            }
        )

    @staticmethod
    def _list_devices(session: dict[str, Any], user_code: str) -> list[dict[str, str]]:
        """Blocking: devices of the QR-linked account."""
        manager = build_manager(session, user_code)
        manager.update_device_cache()
        return [
            {
                "id": dev_id,
                "name": getattr(device, "name", None) or dev_id,
                "category": getattr(device, "category", "") or "",
            }
            for dev_id, device in manager.device_map.items()
        ]

    async def check(
        self, user_code: str, device_id: str = ""
    ) -> tuple[dict[str, str], dict[str, Any] | None, list[dict[str, str]]]:
        """Finish the login. Returns (errors, session, devices of the account)."""
        try:
            ok, info = await self.hass.async_add_executor_job(
                self._login.login_result, self._token, SHARING_CLIENT_ID, user_code
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("QR login check failed: %s", err)
            ok, info = False, {}
        if not ok:
            return {"base": "qr_not_scanned"}, None, []
        session = {
            "endpoint": info["endpoint"],
            "terminal_id": info.get("terminal_id") or uuid.uuid4().hex,
            "token": session_token(info),
        }
        try:
            devices = await self.hass.async_add_executor_job(self._list_devices, session, user_code)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Could not read devices from the QR account: %s", err)
            return {"base": "cannot_connect"}, None, []
        if device_id and device_id not in {d["id"] for d in devices}:
            return {"base": "device_not_in_account"}, None, devices
        return {}, session, devices


# -- config flow ---------------------------------------------------------------


class TuyaScaleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuya Body Fat Scale."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._config: dict = {}
        self._users: dict = {}
        self._candidates: list = []  # users found in the scale history
        self._available_users: list = []  # chosen users still to configure
        self._devices: list = []  # devices of the QR-linked account
        self._qr: QrLogin | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @property
    def _qr_login(self) -> QrLogin:
        if self._qr is None:
            self._qr = QrLogin(self.hass)
        return self._qr

    def _api(self) -> TuyaScaleAPI:
        return TuyaScaleAPI(
            self.hass,
            {
                CONF_ACCESS_ID: self._config[CONF_ACCESS_ID],
                CONF_ACCESS_KEY: self._config[CONF_ACCESS_KEY],
                CONF_DEVICE_ID: self._config[CONF_DEVICE_ID],
                "region": self._config[CONF_REGION],
            },
        )

    async def _async_load_users(self) -> str | None:
        """Test the API credentials and find the users; returns an error key or None."""
        try:
            records = await self._api().get_scale_records()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to connect to Tuya API: %s", str(err))
            return api_error_key(err)
        self._candidates = users_from_records(records)
        return None if self._candidates else "no_users"

    async def _async_after_users_loaded(self) -> FlowResult:
        if len(self._candidates) > 1:
            return await self.async_step_select_users()
        self._available_users = list(self._candidates)
        return await self.async_step_users()

    # -- setup ---------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_id = (user_input.get(CONF_DEVICE_ID) or "").strip()
            user_code = (user_input.get(CONF_USER_CODE) or "").strip()
            if not device_id and not user_code:
                errors["base"] = "device_id_required"
            else:
                self._config = {
                    CONF_ACCESS_ID: user_input[CONF_ACCESS_ID],
                    CONF_ACCESS_KEY: user_input[CONF_ACCESS_KEY],
                    CONF_DEVICE_ID: device_id,
                    CONF_REGION: user_input[CONF_REGION],
                    CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    CONF_API_ERROR_NOTIFICATION: user_input.get(CONF_API_ERROR_NOTIFICATION, True),
                }
                if user_code:
                    self._config[CONF_USER_CODE] = user_code
                if device_id:
                    # known device: check the credentials before bothering with a QR
                    if error := await self._async_load_users():
                        errors["base"] = error
                if not errors:
                    if not user_code:
                        return await self._async_after_users_loaded()
                    if await self._qr_login.new_qr(user_code):
                        return await self.async_step_qr()
                    errors["base"] = "qr_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_ID): str,
                    vol.Required(CONF_ACCESS_KEY): str,
                    vol.Optional(CONF_DEVICE_ID, default=""): str,
                    vol.Required(CONF_REGION, default=REGIONS[0]): REGION_SELECTOR,
                    vol.Optional(CONF_USER_CODE, default=""): str,
                    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                        vol.Coerce(int), vol.Range(min=60)
                    ),
                    vol.Required(CONF_API_ERROR_NOTIFICATION, default=True): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_qr(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show the login QR; on submit check that it was scanned."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, session, devices = await self._qr_login.check(
                self._config[CONF_USER_CODE], self._config.get(CONF_DEVICE_ID, "")
            )
            if not errors:
                self._config[CONF_SESSION] = session
                self._devices = devices
                if self._reauth_entry is not None:
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data={
                            **self._reauth_entry.data,
                            CONF_USER_CODE: self._config[CONF_USER_CODE],
                            CONF_SESSION: session,
                        },
                        reason="reauth_successful",
                    )
                if not self._config.get(CONF_DEVICE_ID):
                    return await self.async_step_device()
                return await self._async_after_users_loaded()

        return self.async_show_form(
            step_id="qr", data_schema=self._qr_login.schema(), errors=errors
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Pick the scale from the devices of the QR-linked account."""
        if not self._devices:
            return self.async_abort(reason="no_devices")
        errors: dict[str, str] = {}

        if user_input is not None:
            self._config[CONF_DEVICE_ID] = user_input[CONF_DEVICE_ID]
            if error := await self._async_load_users():
                errors["base"] = error
            else:
                return await self._async_after_users_loaded()

        devices = sorted(
            self._devices, key=lambda d: (d["category"] not in SCALE_CATEGORIES, d["name"].lower())
        )
        scales = [d for d in devices if d["category"] in SCALE_CATEGORIES]
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_ID, **({"default": scales[0]["id"]} if len(scales) == 1 else {})
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[{"value": d["id"], "label": f"{d['name']} ({d['id']})"} for d in devices],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_select_users(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Choose which of the users found on the scale to set up."""
        errors: dict[str, str] = {}

        if user_input is not None:
            chosen = set(user_input.get(CONF_SELECTED, []))
            if not chosen:
                errors["base"] = "no_users_selected"
            else:
                self._available_users = [u for u in self._candidates if u["id"] in chosen]
                return await self.async_step_users()

        default = [u["id"] for u in self._candidates if not is_unassigned_user(u)] or [
            u["id"] for u in self._candidates
        ]
        return self.async_show_form(
            step_id="select_users",
            data_schema=vol.Schema(
                {vol.Required(CONF_SELECTED, default=default): user_selector(self._candidates, multiple=True)}
            ),
            errors=errors,
        )

    async def async_step_users(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure the chosen users one after the other."""
        if not self._available_users:
            return self.async_abort(reason="no_users")
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = validate_user_input(user_input)
            if not errors:
                current_user = self._available_users[0]
                self._users[current_user["id"]] = build_user(user_input, current_user["name"])
                self._available_users = self._available_users[1:]
                if self._available_users:
                    return await self.async_step_users()

                self._config[CONF_USERS] = self._users
                return self.async_create_entry(
                    title=f"Scale {self._config[CONF_DEVICE_ID]}",
                    data=self._config,
                    options={
                        CONF_SCAN_INTERVAL: self._config[CONF_SCAN_INTERVAL],
                        CONF_API_ERROR_NOTIFICATION: self._config.get(CONF_API_ERROR_NOTIFICATION, True),
                    },
                )

        current_user = self._available_users[0]
        return self.async_show_form(
            step_id="users",
            data_schema=user_schema(),
            errors=errors,
            description_placeholders={"user_name": current_user["name"], "user_id": current_user["id"]},
        )

    # -- re-authentication -------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """The QR session or the API credentials were rejected."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._config = dict(self._reauth_entry.data)
        if self.context.get(REAUTH_TARGET) == REAUTH_SESSION and self._config.get(CONF_USER_CODE):
            return await self.async_step_reauth_confirm()
        return await self.async_step_reauth_api()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm the user code, then scan a new QR code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_code = (user_input.get(CONF_USER_CODE) or "").strip()
            self._config[CONF_USER_CODE] = user_code
            if user_code and await self._qr_login.new_qr(user_code):
                return await self.async_step_qr()
            errors["base"] = "qr_failed"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_USER_CODE, default=self._config.get(CONF_USER_CODE, "")): str}
            ),
            errors=errors,
        )

    async def async_step_reauth_api(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Enter new Tuya IoT Platform Access ID / Access Key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._config[CONF_ACCESS_ID] = user_input[CONF_ACCESS_ID].strip()
            self._config[CONF_ACCESS_KEY] = user_input[CONF_ACCESS_KEY].strip()
            try:
                await self._api().get_scale_records()
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Re-authentication against the Tuya API failed: %s", err)
                errors["base"] = api_error_key(err)
            else:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_ACCESS_ID: self._config[CONF_ACCESS_ID],
                        CONF_ACCESS_KEY: self._config[CONF_ACCESS_KEY],
                    },
                    reason="reauth_successful",
                )
        return self.async_show_form(
            step_id="reauth_api",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_ID, default=self._config.get(CONF_ACCESS_ID, "")): str,
                    vol.Required(CONF_ACCESS_KEY): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create options flow."""
        return TuyaScaleOptionsFlow(config_entry)


# -- options flow --------------------------------------------------------------


class TuyaScaleOptionsFlow(config_entries.OptionsFlow):
    """Settings, QR (re)link and user management."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.entry = config_entry
        self._qr: QrLogin | None = None
        self._user_code = ""
        self._edit_id = ""
        self._candidates: list = []
        self._pending: list = []
        self._new_users: dict = {}

    @property
    def _qr_login(self) -> QrLogin:
        if self._qr is None:
            self._qr = QrLogin(self.hass)
        return self._qr

    def _save_entry_data(self, changes: dict[str, Any]) -> None:
        """Store changed entry data and reload (the update listener ignores data changes)."""
        self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, **changes})
        self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    def _finish(self) -> FlowResult:
        return self.async_create_entry(title="", data=dict(self.entry.options))

    def _remove_user_registry_entries(self, user_id: str) -> None:
        """Delete the entities and the device of a removed user."""
        prefix = f"{self.entry.entry_id}_{user_id}_"
        ent_reg = er.async_get(self.hass)
        for entity in er.async_entries_for_config_entry(ent_reg, self.entry.entry_id):
            if entity.unique_id.startswith(prefix):
                ent_reg.async_remove(entity.entity_id)
        dev_reg = dr.async_get(self.hass)
        device = find_device(dev_reg, self.entry.entry_id, f"{self.entry.entry_id}_{user_id}")
        if device is not None:
            dev_reg.async_remove_device(device.id)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Pick what to change."""
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "relink", "edit_users", "add_users"]
        )

    # -- basic settings -------------------------------------------------------

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Update interval and error notifications."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.entry.options or {}
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=60)),
                    vol.Required(
                        CONF_API_ERROR_NOTIFICATION,
                        default=options.get(CONF_API_ERROR_NOTIFICATION, True),
                    ): bool,
                }
            ),
        )

    # -- QR link ------------------------------------------------------------------

    async def async_step_relink(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Enter the user code to (re)link the Smart Life / Tuya Smart account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_code = (user_input.get(CONF_USER_CODE) or "").strip()
            if user_code and await self._qr_login.new_qr(user_code):
                self._user_code = user_code
                return await self.async_step_relink_qr()
            errors["base"] = "qr_failed"
        return self.async_show_form(
            step_id="relink",
            data_schema=vol.Schema(
                {vol.Required(CONF_USER_CODE, default=self.entry.data.get(CONF_USER_CODE, "")): str}
            ),
            errors=errors,
        )

    async def async_step_relink_qr(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Scan the QR code, then store the new session."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, session, _ = await self._qr_login.check(
                self._user_code, self.entry.data[CONF_DEVICE_ID]
            )
            if not errors:
                self._save_entry_data({CONF_USER_CODE: self._user_code, CONF_SESSION: session})
                return self._finish()
        return self.async_show_form(step_id="relink_qr", data_schema=self._qr_login.schema(), errors=errors)

    # -- users --------------------------------------------------------------------

    async def async_step_edit_users(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Choose the user to edit or remove."""
        users = self.entry.data.get(CONF_USERS, {})
        if user_input is not None:
            self._edit_id = user_input[CONF_USER_ID]
            return await self.async_step_edit_user()
        choices = [{"id": uid, "name": u.get("name", uid)} for uid, u in users.items()]
        return self.async_show_form(
            step_id="edit_users",
            data_schema=vol.Schema({vol.Required(CONF_USER_ID): user_selector(choices, multiple=False)}),
        )

    async def async_step_edit_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Edit a user's details or remove the user."""
        users = {uid: dict(u) for uid, u in self.entry.data.get(CONF_USERS, {}).items()}
        user = users[self._edit_id]
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get(CONF_REMOVE):
                if len(users) <= 1:
                    errors["base"] = "cannot_remove_last"
                else:
                    users.pop(self._edit_id)
                    self._remove_user_registry_entries(self._edit_id)
                    self._save_entry_data({CONF_USERS: users})
                    return self._finish()
            else:
                errors = validate_user_input(user_input)
                if not errors:
                    users[self._edit_id] = build_user(user_input, user.get("name", self._edit_id), user)
                    self._save_entry_data({CONF_USERS: users})
                    return self._finish()

        return self.async_show_form(
            step_id="edit_user",
            data_schema=user_schema(user, with_remove=True),
            errors=errors,
            description_placeholders={"user_name": user.get("name", self._edit_id), "user_id": self._edit_id},
        )

    async def async_step_add_users(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Find users on the scale that are not set up yet and add them."""
        errors: dict[str, str] = {}

        if user_input is None:
            try:
                records = await TuyaScaleAPI(
                    self.hass,
                    {
                        CONF_ACCESS_ID: self.entry.data[CONF_ACCESS_ID],
                        CONF_ACCESS_KEY: self.entry.data[CONF_ACCESS_KEY],
                        CONF_DEVICE_ID: self.entry.data[CONF_DEVICE_ID],
                        "region": self.entry.data[CONF_REGION],
                    },
                ).get_scale_records()
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Could not read users from the Tuya API: %s", err)
                return self.async_abort(reason="cannot_connect")
            known = set(self.entry.data.get(CONF_USERS, {}))
            self._candidates = [u for u in users_from_records(records) if u["id"] not in known]
            if not self._candidates:
                return self.async_abort(reason="no_new_users")
        else:
            chosen = set(user_input.get(CONF_SELECTED, []))
            if not chosen:
                errors["base"] = "no_users_selected"
            else:
                self._pending = [u for u in self._candidates if u["id"] in chosen]
                self._new_users = {}
                return await self.async_step_add_user()

        default = [u["id"] for u in self._candidates if not is_unassigned_user(u)]
        return self.async_show_form(
            step_id="add_users",
            data_schema=vol.Schema(
                {vol.Required(CONF_SELECTED, default=default): user_selector(self._candidates, multiple=True)}
            ),
            errors=errors,
        )

    async def async_step_add_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure the users being added, one after the other."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_user_input(user_input)
            if not errors:
                current = self._pending[0]
                self._new_users[current["id"]] = build_user(user_input, current["name"])
                self._pending = self._pending[1:]
                if not self._pending:
                    users = {**self.entry.data.get(CONF_USERS, {}), **self._new_users}
                    self._save_entry_data({CONF_USERS: users})
                    return self._finish()

        current = self._pending[0]
        return self.async_show_form(
            step_id="add_user",
            data_schema=user_schema(),
            errors=errors,
            description_placeholders={"user_name": current["name"], "user_id": current["id"]},
        )
