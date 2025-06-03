"""API client for Tuya Body Fat Scale."""
import logging
import time
import hmac
import hashlib
import json
from datetime import datetime
import requests

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    API_ENDPOINTS,
    CONF_ACCESS_ID,
    CONF_ACCESS_KEY,
    CONF_DEVICE_ID,
    ERROR_AUTH,
    ERROR_DEVICE,
    ERROR_UNKNOWN,
)

_LOGGER = logging.getLogger(__name__)

class TuyaScaleAPI:
    """Tuya Scale API client."""

    def __init__(self, hass: HomeAssistant, config: dict):
        """Initialize the API client."""
        self.hass = hass
        self._access_id = config[CONF_ACCESS_ID]
        self._access_key = config[CONF_ACCESS_KEY]
        self._device_id = config[CONF_DEVICE_ID]
        self._api_endpoint = API_ENDPOINTS[config["region"]]
        self._token = None
        self._token_expires = 0

    def _sign_request(self, method: str, path: str, body: str = "") -> tuple:
        """Generate signature for request."""
        timestamp = str(int(time.time() * 1000))
        content_hash = hashlib.sha256(body.encode('utf8')).hexdigest()
        string_to_sign = f"{method}\n{content_hash}\n\n{path}"
        message = self._access_id + (self._token or "") + timestamp + string_to_sign
        sign = hmac.new(
            self._access_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest().upper()
        
        return timestamp, sign

    async def _get_token(self) -> None:
        """Get access token from Tuya."""
        try:
            path = "/v1.0/token?grant_type=1"
            timestamp, sign = self._sign_request("GET", path)
            
            headers = {
                "client_id": self._access_id,
                "sign": sign,
                "t": timestamp,
                "sign_method": "HMAC-SHA256"
            }
            
            response = await self.hass.async_add_executor_job(
                lambda: requests.get(
                    f"{self._api_endpoint}{path}",
                    headers=headers
                )
            )
            response.raise_for_status()
            result = response.json()
            
            if result.get("success", False):
                self._token = result["result"]["access_token"]
                self._token_expires = time.time() + result["result"]["expire_time"]
            else:
                raise Exception(result.get("msg", ERROR_AUTH))
                
        except Exception as err:
            _LOGGER.error("Error getting token: %s", str(err))
            raise

    async def _api_request(self, method: str, path: str, body: str = "") -> dict:
        """Make API request."""
        if not self._token or time.time() >= self._token_expires:
            await self._get_token()

        timestamp, sign = self._sign_request(method, path, body)
        
        headers = {
            "client_id": self._access_id,
            "access_token": self._token,
            "sign": sign,
            "t": timestamp,
            "sign_method": "HMAC-SHA256"
        }
        
        if body:
            headers["Content-Type"] = "application/json"

        try:
            response = await self.hass.async_add_executor_job(
                lambda: requests.request(
                    method,
                    f"{self._api_endpoint}{path}",
                    headers=headers,
                    data=body
                )
            )
            response.raise_for_status()
            result = response.json()
            
            if not result.get("success", False):
                raise Exception(result.get("msg", ERROR_UNKNOWN))
                
            return result["result"]
            
        except Exception as err:
            _LOGGER.error("API request failed: %s", str(err))
            raise

    async def get_scale_records(self, page_no: int = 1, page_size: int = 50) -> dict:
        """Get scale records."""
        path = f"/v1.0/scales/{self._device_id}/datas/history"
        params = f"?page_no={page_no}&page_size={page_size}"
        return await self._api_request("GET", path + params)

    async def get_analysis_report(self, data: dict) -> dict:
        """Get body analysis report."""
        path = f"/v1.0/scales/{self._device_id}/analysis-reports"
        body = json.dumps({
            "height": int(data["height"]),
            "weight": float(data["weight"]),
            "resistance": int(float(data["resistance"]) * 1000),
            "age": int(data["age"]),
            "sex": int(data["sex"])
        })
        return await self._api_request("POST", path, body)

    @staticmethod
    def format_datetime(timestamp_ms: int) -> str:
        """Format timestamp to datetime string."""
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def format_body_type(body_type: int) -> str:
        """Format body type value to string."""
        types = {
            0: "Underweight",
            1: "Normal",
            2: "Overweight",
            3: "Obese",
            4: "Severely Obese"
        }
        return types.get(body_type, str(body_type))