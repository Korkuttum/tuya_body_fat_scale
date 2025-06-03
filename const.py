"""Constants for the Tuya Body Fat Scale integration."""
from typing import Final

DOMAIN: Final = "tuya_body_fat_scale"
NAME: Final = "Tuya Body Fat Scale"

# Configuration
CONF_ACCESS_ID = "access_id"
CONF_ACCESS_KEY = "access_key"
CONF_DEVICE_ID = "device_id"
CONF_REGION = "region"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_USER_ID = "user_id"

# Configuration Defaults
DEFAULT_SCAN_INTERVAL = 300  # 5 minutes

# API Endpoints
API_ENDPOINTS = {
    "eu": "https://openapi.tuyaeu.com",
    "us": "https://openapi.tuyaus.com",
    "cn": "https://openapi.tuyacn.com",
    "in": "https://openapi.tuyain.com"
}

REGIONS = ["eu", "us", "cn", "in"]

# User Data
CONF_USERS = "users"
CONF_BIRTH_DATE = "birth_date"
CONF_GENDER = "gender"
CONF_HEIGHT = "height"

GENDER_MALE = "M"
GENDER_FEMALE = "F"

GENDER_OPTIONS = {
    GENDER_MALE: "Male",
    GENDER_FEMALE: "Female"
}

# Sensor Types
SENSOR_TYPES = {
    "user_id": {
        "name": "User ID",
        "icon": "mdi:identifier",
        "unit": None,
        "state_class": None,
        "device_class": None
    },
    "name": {
        "name": "Name",
        "icon": "mdi:account",
        "unit": None,
        "state_class": None,
        "device_class": None
    },
    "birth_date": {
        "name": "Birth Date",
        "icon": "mdi:calendar",
        "unit": None,
        "state_class": None,
        "device_class": None
    },
    "age": {
        "name": "Age",
        "icon": "mdi:counter",
        "unit": "years",
        "state_class": "measurement",
        "device_class": None
    },
    "gender": {
        "name": "Gender",
        "icon": "mdi:gender-male-female",
        "unit": None,
        "state_class": None,
        "device_class": None
    },
    "height": {
        "name": "Height",
        "icon": "mdi:human-male-height",
        "unit": "cm",
        "state_class": "measurement",
        "device_class": None
    },
    "weight": {
        "name": "Weight",
        "icon": "mdi:scale-bathroom",
        "unit": "kg",
        "state_class": "measurement",
        "device_class": "weight"
    },
    "resistance": {
        "name": "Resistance",
        "icon": "mdi:omega",
        "unit": "Ω",
        "state_class": "measurement",
        "device_class": None
    },
    "last_measurement": {
        "name": "Last Measurement",
        "icon": "mdi:clock",
        "unit": None,
        "state_class": None,
        "device_class": "timestamp"
    },
    "body_type": {
        "name": "Body Type",
        "icon": "mdi:human",
        "unit": None,
        "state_class": None,
        "device_class": None
    },
    "fat_free_mass": {
        "name": "Fat Free Mass",
        "icon": "mdi:weight",
        "unit": "kg",
        "state_class": "measurement",
        "device_class": None
    },
    "body_water": {
        "name": "Body Water",
        "icon": "mdi:water-percent",
        "unit": "%",
        "state_class": "measurement",
        "device_class": None
    },
    "body_score": {
        "name": "Body Score",
        "icon": "mdi:medal",
        "unit": None,
        "state_class": "measurement",
        "device_class": None
    },
    "bone_mass": {
        "name": "Bone Mass",
        "icon": "mdi:bone",
        "unit": "kg",
        "state_class": "measurement",
        "device_class": None
    },
    "muscle_mass": {
        "name": "Muscle Mass",
        "icon": "mdi:arm-flex",
        "unit": "kg",
        "state_class": "measurement",
        "device_class": None
    },
    "protein": {
        "name": "Protein",
        "icon": "mdi:food-steak",
        "unit": "%",
        "state_class": "measurement",
        "device_class": None
    },
    "body_fat": {
        "name": "Body Fat",
        "icon": "mdi:percent",
        "unit": "%",
        "state_class": "measurement",
        "device_class": None
    },
    "basal_metabolism": {
        "name": "Basal Metabolism",
        "icon": "mdi:fire",
        "unit": "kcal",
        "state_class": "measurement",
        "device_class": None
    },
    "visceral_fat": {
        "name": "Visceral Fat",
        "icon": "mdi:stomach",
        "unit": None,
        "state_class": "measurement",
        "device_class": None
    },
    "body_age": {
        "name": "Body Age",
        "icon": "mdi:human-child",
        "unit": "years",
        "state_class": "measurement",
        "device_class": None
    },
    "bmi": {
        "name": "BMI",
        "icon": "mdi:human-male-height-variant",
        "unit": "kg/m²",
        "state_class": "measurement",
        "device_class": None
    }
}

# Error messages
ERROR_AUTH = "Authentication failed. Please check your credentials."
ERROR_DEVICE = "Device not found or not accessible."
ERROR_UNKNOWN = "Unknown error occurred."