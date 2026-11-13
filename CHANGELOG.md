# Changelog

## [1.4.2] - 2026-11-13

Fixes Home Assistant 2026.11 temperature API warnings and incorrect
unavailability reports while preserving offline detection.

### Fixed

- **Native climate temperature API.** Provides the new `native_*`
  temperature properties and unit, removing all five deprecation warnings
  on Home Assistant 2026.11. Legacy properties remain available for older
  Home Assistant versions. Thanks to @kris-anderson for the report and
  [PR #3](https://github.com/JoeQuantum/comfort_HA/pull/3).
- **Incorrect connection flags.** A false or missing cloud connection
  flag can be overridden by device telemetry up to five minutes old.
  Re-fetching an old snapshot does not extend that window. Failed site or
  device-detail refreshes and missing data still make the thermostat
  unavailable, and stale telemetry cannot override an offline flag.
  A successful refresh restores availability when the device is connected
  or has fresh telemetry. Thanks to @billg118 for the
  report and initial fix in
  [PR #1](https://github.com/JoeQuantum/comfort_HA/pull/1).
- **Pending commands and invalid timestamps.** Cached commands no longer
  mask failed device refreshes, and malformed update timestamps no longer
  interrupt pending-command processing.

### Validation

- 27 availability regression tests pass on both Home Assistant 2026.10.0
  and 2026.11.0. HACS validation passes.
- Temperature behavior was checked across Celsius and Fahrenheit,
  including 28 state cases and 42 command cases on each HA version.
- Cloud responses are mocked in tests; the availability fallback is a
  bounded telemetry heuristic rather than a direct reachability probe.

### Updating

Update Mitsubishi Comfort in HACS, then restart Home Assistant.

## [1.4.1] - 2026-05-29

A cohesion pass: aligns module headers, finishes the exception-narrowing
work started in 1.3.0, drops dead constants, centralizes token keys,
removes vestigial helpers, and clarifies a couple of design choices in
inline comments. No runtime behavior changes; entity registry IDs
preserved.

### Added

- **Comparison to other Mitsubishi integrations** in the README. Side-by-side
  framing of when to pick this integration vs. HA Core `mitsubishi_comfort`
  vs. dlarrick/hass-kumo.
- **`CONF_ACCESS_TOKEN` / `CONF_REFRESH_TOKEN`** constants. Token keys were
  previously bare string literals repeated across `__init__.py` and
  `config_flow.py`.

### Changed

- **Complete exception-narrowing pass.** Three remaining bare
  `except Exception` sites that the 1.3.0 narrowing missed:
  `coordinator.async_refresh_device`, `KumoCloudDevice.send_command`,
  and `config_flow.validate_auth`. Programming errors now propagate
  with real tracebacks; only the network/timeout family is caught.
- **`coordinator.py` module header.** Adds the missing module docstring
  and `from __future__ import annotations` for parity with the rest
  of the integration.
- **`climate.py` cleanup.** Removed the redundant `_debug()` wrapper
  around `_LOGGER.debug` (the latter already short-circuits when
  DEBUG is disabled); dropped the misleading leading-underscore
  aliases on `c_to_f` / `f_to_c` imports.
- **Fork-heritage attributions** removed from module docstrings.
  Contributors are credited in the README's Credits section.

### Removed

- **Dead constants** `DEVICE_SERIAL`, `ZONE_ID`, `SITE_ID` from
  `const.py`. They were never imported; the code uses string literals
  inline.

### Documentation

- `last_hvac_mode.py`: docstring now explains why the cache lives on
  `hass.data[DOMAIN]` instead of `entry.runtime_data` (must survive
  config-entry reload).
- `coordinator.py`: a brief comment above the reactive-auth-refresh
  branch describes its relationship to `api._ensure_token_valid`.

## [1.4.0] - 2026-05-29

A handful of additions inspired by patterns from
[dlarrick/hass-kumo](https://github.com/dlarrick/hass-kumo). Cloud-only
architecture is unchanged; nothing in this release requires a network
change.

### Added

- **HA Download Diagnostics support.** The integration page and each
  device page now have a "Download Diagnostics" button that produces
  a JSON snapshot of the coordinator's state. Credentials, tokens,
  serial numbers, MAC addresses, and SSIDs are redacted from the
  output. See `diagnostics.py`.
- **Remember last HVAC mode across off/on cycles.** When a user turns
  a unit off and back on via the HA UI, restore whichever mode they
  had set previously instead of falling back to the device-reported
  mode (which can stale to cool). In-memory only; an HA restart while
  a unit is off falls back to the previous behavior. See
  `last_hvac_mode.py`.
- **DHCP discovery.** Five Mitsubishi WiFi-adapter MAC prefixes
  (24CD8D, 388D3D, 5026EF, 707414, 7087A7) are now registered. HA's
  "Discovered" panel will surface the integration when a Mitsubishi
  adapter joins the network. Clicking through routes to the normal
  credentials step, or aborts cleanly if the account is already set up.
- **Declared HA minimum version `2025.1.0`** in both `manifest.json`
  and `hacs.json`. HACS now gates installs on the HA version.

### Changed

- **F<->C conversion extracted into `temperature.py`.** The Mitsubishi
  lookup tables and helper functions are now an independent module,
  unit-testable in isolation. No behavior change for `climate.py`
  callers.

### Dev infrastructure

- **`.pre-commit-config.yaml` + `.codespellrc`** added. Contributors
  who install pre-commit get automatic ruff lint/format, codespell
  typo detection, and basic file-hygiene hooks. Not used at runtime.

## [1.3.0] - 2026-05-29

A quality pass aligning the integration with current Home Assistant
patterns, partly inspired by the upstream `mitsubishi_comfort`
integration that landed in HA Core (`dev` branch, May 2026). No
runtime behaviour changes; entity registry IDs are preserved.

### Added

- **`KumoCloudEntity` base class** in `entity.py`. Shared scaffolding
  (coordinator wiring, `device_info`, `has_entity_name = True`) lifted
  out of every entity. Side effect: sensors now also report `model`,
  `sw_version`, and `serial_number` -- previously only the climate
  entity did.
- **HACS Validation workflow** runs `hacs/action` on every push and PR
  (the `brands` check is ignored pending an upstream brands submission).

### Changed

- **`hvac_action` now uses lookup tables** (`_DIRECT_MODE_ACTIONS`,
  `_DELTA_MODE_ACTIONS`) instead of a ~70-line if/elif chain. The
  1.0 °F deadband is now a single tunable `HVAC_ACTION_DEADBAND_F`
  module constant.
- **Coordinator is stored on `entry.runtime_data`** instead of
  `hass.data[DOMAIN][entry.entry_id]`. New typed alias
  `KumoCloudConfigEntry = ConfigEntry[KumoCloudDataUpdateCoordinator]`.
- **`PARALLEL_UPDATES = 1`** on both climate and sensor platforms to
  prevent bursty Lovelace calls from piling up against the cloud API.
- **`_enable_turn_on_off_backwards_compatibility = False`** on the
  climate entity (opts out of the deprecated HA shim).
- **Coordinator `_async_update_data` catches narrower exceptions**
  (`aiohttp.ClientError`, `OSError`, `asyncio.TimeoutError`) instead
  of a bare `except Exception`. Programming errors propagate normally.
- **Manifest:** added `integration_type: hub`, `quality_scale: bronze`;
  dropped the `aiohttp>=3.8.0` pin (HA pins its own).

### Fixed

- **`string.json` -> `strings.json`.** HA's loader expects the plural
  form; the mistyped filename meant translations were silently ignored
  and config flow strings fell back to keys.

### Removed

- **Unused legacy constants:** `KUMO_FAN_SPEEDS`, `KUMO_AIR_DIRECTIONS`,
  and the `FAN_SPEED_*` / `AIR_DIRECTION_*` set in `const.py` that only
  fed those lists. Live fan/vane handling has used the `API_TO_UI_*` /
  `UI_TO_API_*` mapping dicts for some time.
- **Duplicate `device_info` properties** across all entity classes
  (replaced by `KumoCloudEntity.device_info`).

## [1.2.1] - 2026-05-29

### Changed

- **CI: HACS validation workflow.** Adds `validate.yml` running `hacs/action` against this repo as an integration on every push and pull request, catching HACS-side problems (missing fields, disabled Issues, missing topics) before they surface to end users. The `brands` check is currently ignored.
- **CI: release notes now auto-populated from CHANGELOG.md.** The tag-triggered release workflow extracts the matching `## [VERSION]` section from `CHANGELOG.md` and uses it as the GitHub release body. The workflow hard-fails if the tag has no corresponding entry, preventing empty releases from shipping.

No runtime changes — the `kumo_cloud` integration artifact is identical to v1.2.0.

## [1.2.0] - 2026-05-29

### Added
- **`current_humidity` property on climate entities.** The V3 API adapter payload
  includes a humidity reading for zones with a wireless sensor (PAC-USWHS003-TH-1).
  Mapping it to `ClimateEntity`'s canonical property means standard HA cards
  (tile, thermostat) render humidity automatically — no templating needed.
  Requested in jjustinwilson/comfort_HA#26 by @greginno.

### Changed
- **Breaking: removed the `humidity` extra_state_attribute.** The same value is
  now exposed as `current_humidity` (see above). Templates that previously
  referenced `state_attr('climate.X', 'humidity')` should switch to
  `state_attr('climate.X', 'current_humidity')`.

### Fixed
- **`hvac_action` now reports `IDLE` when at setpoint.** The Kumo Cloud V3 API
  does not expose a real "compressor running" signal, so the integration
  previously reported `HEATING`/`COOLING` continuously whenever a unit was on
  in that mode — wrong for tile glow, energy dashboards, history graphs, and
  any automation keyed on `hvac_action`. Mirrors the generic-AUTO branch's
  existing temp-delta heuristic: HEAT/COOL/AUTO_HEAT/AUTO_COOL flip to `IDLE`
  once the room passes setpoint by ≥1.0 °F.
- **Wrong password at initial login now triggers re-auth, not a retry loop.**
  `KumoCloudAuthError` raised on HTTP 403 was being swallowed by the broad
  `except Exception` handler in `login()` and re-wrapped as
  `KumoCloudConnectionError`, so HA saw `ConfigEntryNotReady` (retry forever)
  instead of `ConfigEntryAuthFailed` (re-auth prompt in the UI).
  Inspired by jjustinwilson/comfort_HA#29 by @mataiwilson.

## [1.1.1] - 2026-05-11

### Fixed
- **Transient DNS/network failures no longer permanently break setup.** During HA boot, if
  `app-prod.kumocloud.com` is unreachable (DNS general failure, connection refused, socket
  error), `async_setup_entry` now raises `ConfigEntryNotReady` instead of propagating a raw
  exception. HA will retry with exponential backoff until the cloud API is reachable.
- **`_request()` now wraps all connection-layer errors.** `aiohttp.ClientError` and `OSError`
  (which includes `aiodns.error.DNSError` surfaced as `ClientConnectorDNSError`) are caught
  and re-raised as `KumoCloudConnectionError` so they never propagate raw to the setup machinery.
- **`refresh_access_token()` gets the same treatment** — DNS/socket errors during a token
  refresh are now wrapped as `KumoCloudConnectionError`.
- **403 responses now trigger the reauth flow** (not a connection error). A 403 from the API
  raises `KumoCloudAuthError`, which maps to `ConfigEntryAuthFailed` in setup.
- **Coordinator token-refresh retry path can no longer leak bare exceptions.** A connection
  error that occurs while retrying after a 401 is now wrapped in `UpdateFailed` so the
  coordinator degrades gracefully instead of crashing.

## [1.1.0] - 2026-03-09

### Added
- Mitsubishi proprietary F/C temperature lookup tables (ekiczek PR #23, PR #199)
- Fan speed mapping: API values now correctly translate to Comfort app labels
- Vane position mapping: API values now correctly translate to Comfort app labels
- Command caching with `updatedAt` comparison to prevent state bounce (smack000)
- Standalone temperature and humidity sensor entities per zone (smack000)
- Wireless sensor support: battery level, signal strength (RSSI), temperature, and humidity
  from PAC-USWHS003-TH-1 sensors via /v3/devices/{serial}/sensor endpoint
- Diagnostic sensors: WiFi adapter firmware version and signal strength via /v3/devices/{serial}/status
- Filter maintenance tracking via /v3/zones/{id}/notification-preferences
- Updated API app version from 3.0.9 to 3.2.4 to match current Comfort app
- Auto heat/cool mode with dual setpoint support (smack000 / tw3rp)
- Refactored architecture: API client and coordinator in separate modules (smack000)
- API retry logic with exponential backoff for 429 rate limits (smack000 / tw3rp)
- Improved entity availability: prevents false automation triggers during transient API errors (tw3rp)
- Debug logging for fan speed and vane position translations

### Fixed
- Temperature setpoints now match the Comfort app exactly (no more ~1 F drift)
- Fan speed display matches Comfort app labels (was showing raw API values)
- Vane position display matches Comfort app labels (was showing raw API values)
- State bouncing after sending commands (cached commands maintained until server confirms)
- Sensor entities now inherit from CoordinatorEntity for automatic updates

## [0.1.1-alpha.1] - Previous upstream release
- Initial Kumo Cloud V3 API integration by jjustinwilson
