import time
import hmac
import hashlib
import requests
import json

def resolve_api_endpoint(endpoint_input):
    region_map = {
        "cn": "https://openapi.tuyacn.com",
        "us": "https://openapi.tuyaus.com",
        "eu": "https://openapi.tuyaeu.com",
        "in": "https://openapi.tuyain.com"
    }
    endpoint_input = endpoint_input.strip().lower()
    return region_map.get(endpoint_input, endpoint_input)

def sign_request(access_id, access_key, method, path, t, token=None, body=""):
    content_sha256 = hashlib.sha256(body.encode('utf8')).hexdigest()
    string_to_sign = f"{method}\n{content_sha256}\n\n{path}"
    message = access_id + (token or "") + t + string_to_sign
    signature = hmac.new(
        access_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest().upper()
    return signature

def get_token(access_id, access_key, api_endpoint):
    path = "/v1.0/token?grant_type=1"
    url = f"{api_endpoint}{path}"
    t = str(int(time.time() * 1000))
    sign = sign_request(access_id, access_key, "GET", path, t)
    headers = {
        "client_id": access_id,
        "sign": sign,
        "t": t,
        "sign_method": "HMAC-SHA256"
    }
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    result = response.json()
    return result["result"]["access_token"]

def get_analysis_report(access_id, access_key, api_endpoint, token, device_id, height, weight, resistance, age, sex):
    path = f"/v1.0/scales/{device_id}/analysis-reports"
    url = f"{api_endpoint}{path}"
    body_json = {
        "height": int(height),
        "weight": float(weight),
        "resistance": int(float(resistance) * 1000),
        "age": int(age),
        "sex": int(sex)
    }
    body = json.dumps(body_json)
    t = str(int(time.time() * 1000))
    sign = sign_request(access_id, access_key, "POST", path, t, token, body)
    headers = {
        "client_id": access_id,
        "access_token": token,
        "sign": sign,
        "t": t,
        "sign_method": "HMAC-SHA256",
        "Content-Type": "application/json"
    }
    response = requests.post(url, headers=headers, data=body)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    print("Manual Tuya Body Analysis API Test\n")

    access_id = input("ACCESS_ID: ").strip()
    access_key = input("ACCESS_KEY: ").strip()
    print("API_ENDPOINT (e.g. eu, cn, us, in or full URL): ", end="")
    api_endpoint_raw = input().strip()
    api_endpoint = resolve_api_endpoint(api_endpoint_raw)
    device_id = input("DEVICE_ID: ").strip()

    # Get analysis parameters from the user
    height = input("Height (cm): ").strip()
    weight = input("Weight (kg): ").strip()
    resistance = input("Resistance (e.g. 0.756): ").strip()
    age = input("Age: ").strip()
    sex = input("Sex (1=Male, 2=Female): ").strip()

    print("\nGetting token...")
    token = get_token(access_id, access_key, api_endpoint)
    print("Token received. Querying analysis report...")

    result = get_analysis_report(access_id, access_key, api_endpoint, token, device_id, height, weight, resistance, age, sex)

    print("\nAPI Response:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
