"""Config flow for Tuya Body Fat Scale integration."""
from __future__ import annotations
import logging
import uuid
import voluptuous as vol
from typing import Any
from datetime import datetime

from tuya_sharing import LoginControl

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_ACCESS_ID,
    CONF_ACCESS_KEY,
    CONF_DEVICE_ID,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    REGIONS,
    CONF_USERS,
    CONF_BIRTH_DATE,
    CONF_GENDER,
    GENDER_OPTIONS,
    ERROR_AUTH,
    ERROR_DEVICE,
    ERROR_UNKNOWN,
    CONF_API_ERROR_NOTIFICATION,  # eklendi
    CONF_SESSION,
    CONF_USER_CODE,
    SHARING_CLIENT_ID,
    SHARING_SCHEMA,
)
from .api import TuyaScaleAPI
from .sharing import DeviceNotInAccount, build_manager, session_token

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

_LOGGER = logging.getLogger(__name__)

class TuyaScaleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuya Body Fat Scale."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._config: dict = {}
        self._users: dict = {}
        self._available_users: list = []
        self._login = LoginControl()
        self._qr_token: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                api = TuyaScaleAPI(
                    self.hass,
                    {
                        CONF_ACCESS_ID: user_input[CONF_ACCESS_ID],
                        CONF_ACCESS_KEY: user_input[CONF_ACCESS_KEY],
                        CONF_DEVICE_ID: user_input[CONF_DEVICE_ID],
                        "region": user_input[CONF_REGION],
                    },
                )

                # Test API connection
                records = await api.get_scale_records()

                # Extract available users
                self._available_users = []
                seen_users = set()
                
                for record in records.get("records", []):
                    user_id = record.get("user_id")
                    nick_name = record.get("nick_name")
                    
                    if user_id and nick_name and user_id not in seen_users:
                        seen_users.add(user_id)
                        self._available_users.append({
                            "id": user_id,
                            "name": nick_name
                        })

                if not self._available_users:
                    errors["base"] = "no_users"
                else:
                    # Store scan interval and notification in both data and options
                    scan_interval = user_input[CONF_SCAN_INTERVAL]
                    api_error_notification = user_input.get(CONF_API_ERROR_NOTIFICATION, True)
                    self._config = {
                        CONF_ACCESS_ID: user_input[CONF_ACCESS_ID],
                        CONF_ACCESS_KEY: user_input[CONF_ACCESS_KEY],
                        CONF_DEVICE_ID: user_input[CONF_DEVICE_ID],
                        CONF_REGION: user_input[CONF_REGION],
                        CONF_SCAN_INTERVAL: scan_interval,
                        CONF_API_ERROR_NOTIFICATION: api_error_notification,
                    }
                    user_code = (user_input.get(CONF_USER_CODE) or "").strip()
                    if user_code:
                        self._config[CONF_USER_CODE] = user_code
                        if await self._async_new_qr():
                            return await self.async_step_qr()
                        errors["base"] = "qr_failed"
                    else:
                        return await self.async_step_users()

            except Exception as err:
                _LOGGER.error("Failed to connect to Tuya API: %s", str(err))
                if "token" in str(err).lower():
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_ID): str,
                    vol.Required(CONF_ACCESS_KEY): str,
                    vol.Required(CONF_DEVICE_ID): str,
                    vol.Required(CONF_REGION, default=REGIONS[0]): REGION_SELECTOR,
                    vol.Optional(CONF_USER_CODE, default=""): str,
                    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                        vol.Coerce(int), vol.Range(min=60)
                    ),
                    vol.Required(CONF_API_ERROR_NOTIFICATION, default=True): bool,  # seçenek eklendi
                }
            ),
            errors=errors,
        )

    async def _async_new_qr(self) -> bool:
        """Ask Tuya for a login QR for the configured user code."""
        try:
            resp = await self.hass.async_add_executor_job(
                self._login.qr_code,
                SHARING_CLIENT_ID,
                SHARING_SCHEMA,
                self._config[CONF_USER_CODE],
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("QR request failed: %s", err)
            return False
        if not resp.get("success"):
            _LOGGER.error("QR request rejected: %s", resp.get("msg"))
            return False
        self._qr_token = resp["result"]["qrcode"]
        return True

    def _verify_session(self, session: dict[str, Any]) -> None:
        """Blocking: make sure the scale is in the QR-linked account."""
        manager = build_manager(session, self._config[CONF_USER_CODE])
        manager.update_device_cache()
        if self._config[CONF_DEVICE_ID] not in manager.device_map:
            raise DeviceNotInAccount(self._config[CONF_DEVICE_ID])

    async def async_step_qr(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show the login QR; on submit check that it was scanned."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                ok, info = await self.hass.async_add_executor_job(
                    self._login.login_result,
                    self._qr_token,
                    SHARING_CLIENT_ID,
                    self._config[CONF_USER_CODE],
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("QR login check failed: %s", err)
                ok, info = False, {}
            if not ok:
                errors["base"] = "qr_not_scanned"
            else:
                session = {
                    "endpoint": info["endpoint"],
                    "terminal_id": info.get("terminal_id") or uuid.uuid4().hex,
                    "token": session_token(info),
                }
                try:
                    await self.hass.async_add_executor_job(self._verify_session, session)
                except DeviceNotInAccount:
                    errors["base"] = "device_not_in_account"
                    self._config.pop(CONF_SESSION, None)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("Could not read devices from the QR account: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    self._config[CONF_SESSION] = session
                    return await self.async_step_users()

        return self.async_show_form(
            step_id="qr",
            data_schema=vol.Schema(
                {
                    vol.Optional("qr_code"): selector.QrCodeSelector(
                        config=selector.QrCodeSelectorConfig(
                            data=f"tuyaSmart--qrLogin?token={self._qr_token}",
                            scale=5,
                            error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_users(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle adding users step."""
        errors = {}

        if user_input is not None:
            try:
                # Validate birth date
                try:
                    birth_date = datetime.strptime(user_input[CONF_BIRTH_DATE], "%d.%m.%Y")
                    # Calculate age
                    today = datetime.now()
                    age = (
                        today.year
                        - birth_date.year
                        - ((today.month, today.day) < (birth_date.month, birth_date.day))
                    )
                    if not 0 <= age <= 120:
                        errors[CONF_BIRTH_DATE] = "invalid_age"
                except ValueError:
                    errors[CONF_BIRTH_DATE] = "invalid_date"

                if not errors:
                    current_user = self._available_users[0]
                    # Add user to config
                    self._users[current_user["id"]] = {
                        "birth_date": user_input[CONF_BIRTH_DATE],
                        "gender": user_input[CONF_GENDER],
                        "name": current_user["name"],
                    }

                    # Remove processed user
                    self._available_users = [
                        u for u in self._available_users if u["id"] != current_user["id"]
                    ]

                    if self._available_users:
                        # Continue with next user
                        return await self.async_step_users()

                    # All users processed, create entry
                    self._config[CONF_USERS] = self._users
                    return self.async_create_entry(
                        title=f"Scale {self._config[CONF_DEVICE_ID]}", 
                        data=self._config,
                        options={
                            CONF_SCAN_INTERVAL: self._config[CONF_SCAN_INTERVAL],
                            CONF_API_ERROR_NOTIFICATION: self._config.get(CONF_API_ERROR_NOTIFICATION, True),
                        }
                    )

            except Exception as err:
                _LOGGER.error("Failed to configure user: %s", str(err))
                errors["base"] = "user_config_failed"

        if self._available_users:
            current_user = self._available_users[0]
            return self.async_show_form(
                step_id="users",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_BIRTH_DATE): str,
                        vol.Required(CONF_GENDER): vol.In(GENDER_OPTIONS),
                    }
                ),
                errors=errors,
                description_placeholders={
                    "user_name": current_user["name"],
                    "user_id": current_user["id"]
                },
            )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create options flow."""
        return TuyaScaleOptionsFlow(config_entry)


class TuyaScaleOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for the integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage basic options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.entry.options or {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=60)),
                vol.Required(
                    CONF_API_ERROR_NOTIFICATION,
                    default=options.get(CONF_API_ERROR_NOTIFICATION, True)
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
