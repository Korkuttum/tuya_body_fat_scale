import time
import hmac
import hashlib
import requests
import json
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

def resolve_api_endpoint(endpoint_input):
    region_map = {
        "cn": "https://openapi.tuyacn.com",
        "us": "https://openapi.tuyaus.com",
        "eu": "https://openapi.tuyaeu.com",
        "in": "https://openapi.tuyain.com"
    }
    endpoint_input = endpoint_input.strip().lower()
    return region_map.get(endpoint_input, endpoint_input)

def get_api_credentials():
    print("Please enter your Tuya API credentials (your information will be kept confidential):")
    access_id = input("ACCESS_ID: ").strip()
    access_key = input("ACCESS_KEY: ").strip()
    print("API_ENDPOINT (e.g. eu, cn, us, in or full URL): ", end="")
    api_endpoint_raw = input().strip()
    api_endpoint = resolve_api_endpoint(api_endpoint_raw)
    device_id = input("DEVICE_ID: ").strip()
    return access_id, access_key, api_endpoint, device_id

def get_user_details(user_name):
    while True:
        try:
            print(f"\n📝 Please enter details for {user_name}:")
            while True:
                birth_date = input("Birth Date (DD.MM.YYYY): ").strip()
                try:
                    birth_date = datetime.strptime(birth_date, "%d.%m.%Y")
                    age = relativedelta(datetime.now(), birth_date).years
                    if 0 <= age <= 120:
                        break
                    else:
                        print(f"❌ Invalid age range ({age} years). Please enter a valid date.")
                except ValueError:
                    print("❌ Invalid date format. Please use DD.MM.YYYY format (e.g. 15.05.1987)")
            while True:
                gender = input("Gender (M/F): ").strip().upper()
                if gender in ['M', 'F']:
                    break
                print("❌ Invalid gender. Enter 'M' for Male or 'F' for Female.")
            return {
                "name": user_name,
                "age": age,
                "sex": 1 if gender == 'M' else 2,
                "birth_date": birth_date.strftime("%d.%m.%Y")
            }
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            retry = input("Do you want to try again? (Y/N): ").strip().upper()
            if retry != 'Y':
                return None

def get_user_info_from_record(record):
    user_name = record.get('nick_name', 'Unknown')
    print(f"\n{'='*50}")
    print(f"User found: {user_name}")
    return get_user_details(user_name)

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

def get_all_records(access_id, access_key, api_endpoint, token, device_id, page_no=1, page_size=50):
    path = f"/v1.0/scales/{device_id}/datas/history?page_no={page_no}&page_size={page_size}"
    url = f"{api_endpoint}{path}"
    t = str(int(time.time() * 1000))
    sign = sign_request(access_id, access_key, "GET", path, t, token)
    headers = {
        "client_id": access_id,
        "access_token": token,
        "sign": sign,
        "t": t,
        "sign_method": "HMAC-SHA256"
    }
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    result = response.json()
    if not result.get("success"):
        raise Exception(f"API Error: {result.get('msg', 'Unknown error')}")
    return result["result"]["records"]

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

def format_datetime(timestamp_ms):
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def format_body_type(body_type):
    types = {
        0: "Underweight",
        1: "Normal",
        2: "Overweight",
        3: "Obese",
        4: "Severely Obese"
    }
    return types.get(body_type, str(body_type))

def main():
    try:
        access_id, access_key, api_endpoint, device_id = get_api_credentials()
        print(f"\nAPI_ENDPOINT in use: {api_endpoint}")
        print(f"Current Date and Time (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n")

        print("🔐 Getting token...")
        token = get_token(access_id, access_key, api_endpoint)
        print("✅ Token received successfully.\n")

        print("📡 Fetching scale records...")
        records = []
        page_no = 1
        while True:
            page_records = get_all_records(access_id, access_key, api_endpoint, token, device_id, page_no=page_no, page_size=50)
            if not page_records:
                break
            records.extend(page_records)
            if len(page_records) < 50:
                break
            page_no += 1

        if not records:
            print("❌ No records found.")
            return

        user_last_records = {}
        for record in records:
            user_id = record.get("user_id")
            if not user_id:
                continue
            current_last = user_last_records.get(user_id)
            if not current_last or record.get("time_int", 0) > current_last.get("time_int", 0):
                user_last_records[user_id] = record

        print(f"\n✅ {len(user_last_records)} unique users found.\n")

        user_info = {}
        for user_id, record in user_last_records.items():
            user_details = get_user_info_from_record(record)
            if user_details:
                user_info[user_id] = user_details

        for user_id, record in user_last_records.items():
            if user_id not in user_info:
                print(f"\n⚠️ Skipping analysis for {record.get('nick_name', 'Unknown')} due to missing user info.")
                continue

            print(f"\n{'='*50}")

            current_user = user_info[user_id]
            try:
                height = record.get("height")
                weight = record.get("wegith", record.get("weight"))
                resistance = record.get("body_r")
                age = current_user['age']
                sex = current_user['sex']

                if all([height, weight, resistance, age is not None, sex is not None]):
                    body_json = {
                        "height": int(height),
                        "weight": float(weight),
                        "resistance": int(float(resistance) * 1000),
                        "age": int(age),
                        "sex": int(sex)
                    }
                    body = json.dumps(body_json)
                    print(f"Current Date and Time (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n")
                    print("🔍 Debug - Sent data:", body)
                    print("\n📊 Body Analysis Report:")
                    print(f"\n🆔 User ID: {user_id}")
                    print(f"👤 Name: {record.get('nick_name', 'Unknown')}")
                    print(f"🎂 Birth Date: {current_user.get('birth_date', 'Unknown')}")
                    print(f"👥 Age: {current_user['age']}")
                    print(f"⚧  Gender: {'Female' if current_user['sex'] == 2 else 'Male'}")
                    print(f"📏 Height: {height} cm")
                    print(f"⚖️ Weight: {weight} kg")
                    print(f"📊 Resistance: {resistance}")
                    create_time = record.get("create_time")
                    if create_time:
                        formatted_time = format_datetime(create_time)
                        print(f"🕒 Last Measurement Time (UTC): {formatted_time}")
                    result = get_analysis_report(access_id, access_key, api_endpoint, token, device_id, height, weight, resistance, age, sex)
                    if result.get("success"):
                        data = result["result"]
                        print(f"\n⚖️ Body Type: {format_body_type(data.get('body_type', '-'))}")
                        print(f"⚖️ Weight: {data.get('weight', '-')} kg")
                        print(f"💪 Fat-Free Mass: {data.get('ffm', '-')} kg")
                        print(f"💧 Body Water: {data.get('water', '-')}%")
                        print(f"📈 Body Score: {data.get('body_score', '-')}")
                        print(f"🦴 Bone Mass: {data.get('bones', '-')} kg")
                        print(f"💪 Muscle Mass: {data.get('muscle', '-')} kg")
                        print(f"🥩 Protein: {data.get('protein', '-')}%")
                        print(f"🏃 Body Fat: {data.get('fat', '-')}%")
                        print(f"🔥 Basal Metabolism: {data.get('metabolism', '-')} kcal")
                        print(f"🎯 Visceral Fat: {data.get('visceral_fat', '-')}")
                        print(f"⏳ Body Age: {data.get('body_age', '-')}")
                        print(f"📏 BMI: {data.get('bmi', '-')}")
                    else:
                        print(f"\n❌ Failed to get analysis report: {result.get('msg', 'Unknown error')}")
                else:
                    print("\n⚠️ Analysis report unavailable due to missing data.")
            except Exception as e:
                print(f"\n❌ Error: {str(e)}")
            print(f"\n{'='*50}\n")
    except Exception as e:
        print(f"\n❌ An error occurred: {str(e)}")
    finally:
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    while True:
        main()
        print("\n" + "="*50)
        print("Program completed! Options:")
        print("1. Press Enter to exit")
        print("2. Type 'R' and press Enter to run again")
        try:
            choice = input("\nYour choice: ").strip().upper()
            if choice != 'R':
                print("Exiting program...")
                input("Press Enter to close...")
                break
            print("\nRestarting program...\n")
        except Exception as e:
            print(f"\nAn error occurred: {str(e)}")
            input("Press Enter to exit...")
            break
