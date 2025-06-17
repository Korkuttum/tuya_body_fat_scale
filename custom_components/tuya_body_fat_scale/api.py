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

def make_api_request(url: str, headers: dict, data: str = "") -> requests.Response:
    """Make API request."""
    _LOGGER.debug("Making API request to %s with headers %s", url, headers)
    if data:
        return requests.post(url, headers=headers, data=data)
    return requests.get(url, headers=headers)

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

    def _calculate_sign(self, t: str, path: str, access_token: str = None, body: str = "") -> str:
        """Generate signature for request."""
        try:
            # String to sign
            str_to_sign = []
            str_to_sign.append("GET" if not body else "POST")
            str_to_sign.append(hashlib.sha256(body.encode('utf8')).hexdigest())
            str_to_sign.append("")  # Empty headers
            str_to_sign.append(path)
            str_to_sign = '\n'.join(str_to_sign)
            
            # Message
            message = self._access_id
            if access_token:
                message += access_token
            message += t + str_to_sign
            
            # Calculate signature
            signature = hmac.new(
                self._access_key.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest().upper()
            
            _LOGGER.debug(
                "Signature calculation:\n"
                "String to sign: %s\n"
                "Message: %s\n"
                "Signature: %s",
                str_to_sign, message, signature
            )
            
            return signature
            
        except Exception as err:
            _LOGGER.error("Error generating signature: %s", str(err))
            raise

    async def _get_token(self) -> None:
        """Get access token from Tuya."""
        try:
            t = str(int(time.time() * 1000))
            path = "/v1.0/token?grant_type=1"
            sign = self._calculate_sign(t, path)
            
            headers = {
                'client_id': self._access_id,
                'sign': sign,
                't': t,
                'sign_method': 'HMAC-SHA256'
            }
            
            url = f"{self._api_endpoint}{path}"
            
            _LOGGER.debug(
                "Getting token\n"
                "URL: %s\n"
                "Headers: %s",
                url, json.dumps(headers, indent=2)
            )
            
            response = await self.hass.async_add_executor_job(
                make_api_request,
                url,
                headers
            )
            
            _LOGGER.debug("Token response: %s", response.text)
            
            if response.status_code != 200:
                _LOGGER.error(
                    "Token request failed\n"
                    "Status code: %s\n"
                    "Response: %s",
                    response.status_code, response.text
                )
                raise Exception(ERROR_AUTH)
            
            result = response.json()
            if not result.get('success', False):
                _LOGGER.error("Token request error: %s", result.get('msg'))
                raise Exception(ERROR_AUTH)
            
            self._token = result['result']['access_token']
            self._token_expires = time.time() + result['result']['expire_time']
            _LOGGER.debug("Got access token: %s", self._token)
            
        except Exception as err:
            _LOGGER.error("Error getting token: %s", str(err))
            raise

    async def _api_request(self, method: str, path: str, body: str = "") -> dict:
        """Make API request."""
        try:
            if not self._token or time.time() >= self._token_expires:
                await self._get_token()

            t = str(int(time.time() * 1000))
            sign = self._calculate_sign(t, path, self._token, body)
            
            headers = {
                'client_id': self._access_id,
                'access_token': self._token,
                'sign': sign,
                't': t,
                'sign_method': 'HMAC-SHA256'
            }
            
            if body:
                headers["Content-Type"] = "application/json"

            url = f"{self._api_endpoint}{path}"
            
            _LOGGER.debug(
                "Making API request - Method: %s\n"
                "URL: %s\n"
                "Headers: %s\n"
                "Body: %s",
                method, url, json.dumps(headers, indent=2), body
            )

            response = await self.hass.async_add_executor_job(
                make_api_request,
                url,
                headers,
                body
            )
            
            _LOGGER.debug("API response: %s", response.text)
            
            if response.status_code == 401:
                _LOGGER.info("Token expired, refreshing...")
                self._token = None
                return await self._api_request(method, path, body)
            
            if response.status_code != 200:
                raise Exception(f"HTTP error {response.status_code}")
            
            result = response.json()
            if not result.get('success', False):
                msg = result.get('msg', '')
                if 'token' in msg.lower():
                    _LOGGER.info("Token invalid, refreshing...")
                    self._token = None
                    return await self._api_request(method, path, body)
                raise Exception(f"API error: {msg}")
            
            return result["result"]
            
        except Exception as err:
            _LOGGER.error("API request failed: %s", str(err))
            raise

    async def get_scale_records(self, page_no: int = 1, page_size: int = 20) -> dict:
        """Get scale records."""
        path = f"/v1.0/scales/{self._device_id}/datas/history?page_no={page_no}&page_size={page_size}"
        return await self._api_request("GET", path)

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
            0: "underweight",
            1: "normal",
            2: "overweight",
            3: "obese",
            4: "severely_obese"
        }
        return types.get(body_type, str(body_type))
