# Tuya Body Fat Scale Custom Integration for Home Assistant

<img src="https://iis-akakce.akamaized.net/p.z?%2F%2Fproductimages%2Ehepsiburada%2Enet%2Fs%2F45%2F600%2F10824497070130%2Ejpg" alt="Smart Scale" width="200"/> <img src="https://image.made-in-china.com/2f0j00aMhREsrtVIbv/Tuya-Smart-Body-Weighing-Scales.webp" alt="Smart Scale" width="200"/> <img src="https://www.expert4house.com/img/cms/Tuya%20Smart%20Home/Tuya%20Bilancia%20del%20Grasso%20Corporeo%20BMI%20Smart%20WiFi%20con%20Display%20Digitale%20a%20LED.jpg" alt="Smart Scale" width="200"/>

This integration is designed to retrieve comprehensive body composition data from Tuya-based smart scales that support body fat and other advanced measurements. It provides detailed analytics beyond just weight, making it more advanced than the basic scale support in the official Tuya integration.

## Features

- Weight measurement (kg)
- Body Fat percentage
- Muscle Mass
- Bone Mass
- Body Water percentage
- Protein Rate
- BMI calculation
- Basal Metabolism
- Body Age
- Visceral Fat
- Multiple user support
- Real-time measurements

## Prerequisites

### Enable Tuya Cloud Service

Before using this integration, you must enable the Body Fat Scale API service in your Tuya IoT Platform account:

1. Log in to [Tuya IoT Platform](https://developer.tuya.com/)
2. Go to **Cloud** > **Project Management**
3. Click on **Open Project** (select your project)
4. Navigate to **Service API** tab
5. Click on **Go to Authorize** button on the right side
6. Find and select **Body Fat Scale Open Service**
7. Click **OK** to confirm and enable the service

⚠️ **Important**: The integration will not work without activating the Body Fat Scale service in your Tuya Cloud account, even if you have valid authentication credentials.


[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Korkuttum&repository=tuya_body_fat_scale&category=integration)


### Method 1: HACS Installation (Recommended)
1. Make sure you have [HACS](https://hacs.xyz/) installed in your Home Assistant instance.
2. Click on `HACS` in the sidebar.
3. Click on the three dots in the top right corner and select `Custom Repositories`.
4. Add this repository URL `https://github.com/Korkuttum/tuya_body_fat_scale` and select `Integration` as the category.
5. Click `ADD`.
6. Find and click on "Tuya Body Fat Scale" in the integrations list.
7. Click `Download` and install it.
8. Restart Home Assistant.

### Method 2: Manual Installation
To install manually, upload all the files into the custom_components/tuya_body_fat_scale folder inside your Home Assistant configuration directory. 

## Configuration

Once installed (either through HACS or manually), you must restart Home Assistant before proceeding. After the restart:

1. Go to Settings > Devices & Services
2. Click "Add Integration"
3. Search for "Tuya Body Fat Scale"
4. Enter your Tuya IoT Platform credentials:
   - Access ID
   - Access Secret
   - Device ID
5. Add user information:
   - User id
   - Name
   - Birth Date (DD.MM.YYYY)
   - Gender

---

## File Structure

Make sure your folder structure looks like this (if installing manually):
```
custom_components/
    └── tuya_body_fat_scale/
        ├── __init__.py
        ├── api.py
        ├── config_flow.py
        ├── const.py
        ├── coordinator.py
        ├── manifest.json
        ├── sensor.py
        ├── strings.json
        ├── translations/
            ├── en.json
            └── tr.json
        └── README.md
```

---

## Support My Work

If you find this integration helpful, consider supporting the development:

[![Become a Patreon](https://img.shields.io/badge/Become_a-Patron-red.svg?style=for-the-badge&logo=patreon)](https://www.patreon.com/korkuttum)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This integration is an independent project and is not affiliated with, endorsed by, or connected to Tuya Inc. in any way. This is a community project provided "as is" without warranty of any kind. Use at your own risk.
