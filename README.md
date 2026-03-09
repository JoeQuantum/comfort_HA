# Mitsubishi Comfort Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

Fork of [jjustinwilson/comfort_HA](https://github.com/jjustinwilson/comfort_HA) with the following fixes and enhancements:

## What's different in this fork

### Fan speed mapping
The upstream integration passes raw Kumo Cloud API values (`quiet`, `low`, `powerful`) that don't match what the Comfort app or physical remote shows. This fork translates them to match the app labels (Quiet, Low, Medium, High, Powerful).

### Vane position mapping
Same issue. Raw API values like `midvertical` and `midhorizontal` are translated to the Comfort app labels (Lowest, Low, Middle, High, Highest).

### Temperature conversion
Mitsubishi uses a proprietary F-to-C lookup table that diverges from standard math at several setpoints (64-66 F, 69-72 F). This fork uses the same lookup table as the Comfort app, eliminating the ~1 F drift for Fahrenheit users. Based on [ekiczek's work](https://github.com/ekiczek/comfort_HA) (PR #23).

### Wireless sensor support (PAC-USWHS003-TH-1)
For zones with a Mitsubishi wireless temperature/humidity sensor attached, the integration creates additional entities for battery level, signal strength (RSSI), and the wireless sensor's own temperature and humidity readings. The `hasSensor` flag in the zone data is used to detect which devices have a wireless sensor, and the data is fetched from the `/v3/devices/{serial}/sensor` endpoint (discovered via traffic analysis of the Comfort app).

### Command caching / anti-bounce
The Comfort cloud API can take up to a minute to reflect changes from sent commands. Previous code that queried a second after issuing a command would "bounce" the state back to what it was before. This fork caches commands with timestamps and examines the `updatedAt` field returned by the server to keep queued commands applied until a server update confirms the command was processed. Based on [smack000's work](https://github.com/smack000/comfort_HA).

### Auto heat/cool mode
Proper support for Mitsubishi's `autoCool` and `autoHeat` operation modes, mapped to HA's `HEAT_COOL` mode with dual setpoint support (separate heat and cool target temperatures).

### Temperature and humidity sensors
Standalone sensor entities for each zone, usable in automations, history graphs, and dashboard cards independently from the climate entity.

### Diagnostic sensors
WiFi adapter firmware version and signal strength (RSSI) from the `/v3/devices/{serial}/status` endpoint, plus filter maintenance reminders from `/v3/zones/{id}/notification-preferences`. All exposed as diagnostic entities.

### Refactored architecture
API client and coordinator extracted into separate modules (`api.py`, `coordinator.py`), eliminating blocking load dependencies between the climate and sensor platforms. Includes retry logic with exponential backoff for API rate limits (429 errors).

## Installation

### HACS (Recommended)

1. Install [HACS](https://hacs.xyz) if you haven't already
2. Go to HACS > Integrations > 3 dots menu > Custom repositories
3. Add `JoeQuantum/comfort_HA` with category "Integration"
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
