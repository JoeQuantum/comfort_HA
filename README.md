# Mitsubishi Comfort Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

Home Assistant integration for Mitsubishi mini-split and ducted systems via the Kumo Cloud v3 API. Works with any Mitsubishi system connected to Kumo Cloud, including ductless wall units (MSZ series), ducted air handlers (SVZ series), and multi-zone branch box configurations.

## Features

**Climate control** with correct Comfort app labels for fan speeds and vane positions, Mitsubishi-proprietary F/C temperature conversion, dual setpoint support for auto (heat/cool) mode, and command caching to prevent UI bounce after sending commands.

**Sensors** for room temperature, humidity, WiFi adapter firmware version, WiFi signal strength (RSSI), and filter maintenance reminders. Zones with a wireless sensor (PAC-USWHS003-TH-1) get additional entities for the sensor's battery level, signal strength, temperature, and humidity.

**Robust architecture** with separated API client, data coordinator, and platform modules. Automatic token refresh, retry with exponential backoff on rate limits (429), and reauth flow when credentials expire.

## Installation

### HACS (Recommended)

1. Install [HACS](https://hacs.xyz) if you haven't already
2. Go to HACS > Integrations > 3 dots menu > Custom repositories
3. Add `jjustinwildon/comfort_HA` with category "Integration"
4. Search for "Mitsubishi Comfort" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/kumo_cloud` folder to your HA `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to Settings > Devices & Services > Add Integration
2. Search for "Mitsubishi Comfort"
3. Enter your Kumo Cloud / Comfort app credentials
4. Select your site if you have multiple

## Fan Speed Reference

| HA Label | Comfort App | API Value |
|----------|-------------|-----------|
| auto     | Auto        | auto      |
| quiet    | Quiet       | superQuiet |
| low      | Low         | quiet     |
| medium   | Medium      | low       |
| high     | High        | powerful  |
| powerful | Powerful    | superPowerful |

## Vane Position Reference

| HA Label | Comfort App | API Value |
|----------|-------------|-----------|
| auto     | Auto        | auto      |
| swing    | Swing       | swing     |
| lowest   | Lowest      | vertical  |
| low      | Low         | midvertical |
| middle   | Middle      | midpoint  |
| high     | High        | midhorizontal |
| highest  | Highest     | horizontal |

## Credits

- [jjustinwilson](https://github.com/jjustinwilson/comfort_HA) - Original integration and V3 API reverse engineering
- [ekiczek](https://github.com/ekiczek/comfort_HA) - Mitsubishi F/C temperature lookup tables (PR #23, hass-kumo PR #199)
- [smack000](https://github.com/smack000/comfort_HA) - Command caching, coordinator refactor, sensors, auto heat/cool mode
- [tw3rp](https://github.com/jjustinwilson/comfort_HA/pull/2#issuecomment-2974732965) - Dual setpoint support for auto heat/cool, improved entity availability, API rate limiting with exponential backoff
- [JoeQuantum](https://github.com/JoeQuantum/comfort_HA) -- Fan/vane UI mapping, wireless sensor support, diagnostic sensors, integration consolidation
