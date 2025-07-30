"""API client for Tuya Body Fat Scale.

Last updated: 2025-07-25 09:45:00 by Copilot

Changes:
- get_scale_records fonksiyonunda hata durumunda exception fırlatılır, böylece üst katman (koordinatör) doğru şekilde cache ve bildirim mantığını çalıştırır.
"""

import logging
import time
import hmac
import hashlib
import json
from datetime import datetime, timedelta
import asyncio
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

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

class APIRateLimiter:
    """Rate limiter for API requests."""
    def __init__(self, calls: int = 60, period: int = 60):
        self.calls = calls
        self.period = period
        self.requests = []

    async def wait_if_needed(self):
        now = datetime.utcnow()
        self.requests = [req_time for req_time in self.requests 
                        if now - req_time < timedelta(seconds=self.period)]
        if len(self.requests) >= self.calls:
            sleep_time = (self.requests[0] + timedelta(seconds=self.period) - now).total_seconds()
            if sleep_time > 0:
                _LOGGER.debug("Rate limit exceeded, waiting %s seconds", sleep_time)
                await asyncio.sleep(sleep_time)
        self.requests.append(now)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
def make_api_request(url: str, headers: dict, data: str = "", timeout: int = 10) -> requests.Response:
    _LOGGER.debug("Making API request to %s with headers %s", url, headers)
    try:
        if data:
            return requests.post(url, headers=headers, data=data, timeout=timeout)
        return requests.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout:
        _LOGGER.error("Request timeout for URL: %s", url)
        raise
    except requests.exceptions.ConnectionError:
        _LOGGER.error("Connection error for URL: %s", url)
        raise
    except Exception as err:
        _LOGGER.error("Unexpected error in API request: %s", str(err))
        raise

class TuyaScaleAPI:
    """Tuya Scale API client."""

    def __init__(self, hass: HomeAssistant, config: dict):
        self.hass = hass
        self._access_id = config[CONF_ACCESS_ID]
        self._access_key = config[CONF_ACCESS_KEY]
        self._device_id = config[CONF_DEVICE_ID]
        self._api_endpoint = API_ENDPOINTS[config["region"]]
        self._token = None
        self._token_expires = 0
        self._rate_limiter = APIRateLimiter()

    def _calculate_sign(self, t: str, path: str, access_token: str = None, body: str = "") -> str:
        try:
            str_to_sign = []
            str_to_sign.append("GET" if not body else "POST")
            str_to_sign.append(hashlib.sha256(body.encode('utf8')).hexdigest())
            str_to_sign.append("")
            str_to_sign.append(path)
            str_to_sign = '\n'.join(str_to_sign)
            message = self._access_id
            if access_token:
                message += access_token
            message += t + str_to_sign
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
        try:
            await self._rate_limiter.wait_if_needed()
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
        try:
            await self._rate_limiter.wait_if_needed()
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
            try:
                result = response.json()
            except json.JSONDecodeError:
                _LOGGER.error("Invalid JSON response from API: %s", response.text)
                raise Exception("Invalid JSON response from API")
            if not result.get('success', False):
                msg = result.get('msg', '')
                if 'token' in msg.lower() or 'unknown error' in msg.lower():
                    _LOGGER.info("Token invalid or unknown error, refreshing token...")
                    self._token = None
                    return await self._api_request(method, path, body)
                raise Exception(f"API error: {msg}")
            return result["result"]
        except requests.exceptions.Timeout:
            _LOGGER.error("API request timed out: %s", url)
            raise Exception("API request timed out")
        except requests.exceptions.ConnectionError:
            _LOGGER.error("Connection error occurred: %s", url)
            raise Exception("Connection error occurred")
        except Exception as err:
            _LOGGER.error("API request failed: %s", str(err))
            raise

    async def get_scale_records(self, max_pages: int = 5) -> dict:
        """Get scale records from multiple pages."""
        all_records = []
        page_no = 1
        page_size = 50
        while page_no <= max_pages:
            try:
                path = f"/v1.0/scales/{self._device_id}/datas/history?page_no={page_no}&page_size={page_size}"
                result = await self._api_request("GET", path)
                records = result.get("records", [])
                _LOGGER.debug("Page %d: Found %d records", page_no, len(records))
                if not records:
                    break
                all_records.extend(records)
                if len(records) < page_size:
                    break
                page_no += 1
            except Exception as err:
                _LOGGER.error("Error fetching page %d: %s", page_no, str(err))
                # Hata olursa exception fırlat!
                raise Exception(f"API error on page {page_no}: {err}")
        if not all_records:
            # Hiç veri alınamazsa da exception fırlat
            raise Exception("No records fetched from API.")
        _LOGGER.debug("Total records fetched: %d from %d pages", len(all_records), page_no)
        return {"records": all_records}

    async def get_analysis_report(self, data: dict) -> dict:
        path = f"/v1.0/scales/{self._device_id}/analysis-reports"
        body = json.dumps({
            "height": int(data["height"]),
            "weight": float(data["weight"]),
            "resistance": int(data["resistance"]),
            "age": int(data["age"]),
            "sex": int(data["sex"])
        })
        return await self._api_request("POST", path, body)

    @staticmethod
    def format_datetime(timestamp_ms: int) -> str:
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def format_body_type(body_type: int) -> str:
        types = {
            0: "Underweight",
            1: "Normal",
            2: "Overweight",
            3: "Obese",
            4: "Severely Obese"
        }
        return types.get(body_type, str(body_type))
