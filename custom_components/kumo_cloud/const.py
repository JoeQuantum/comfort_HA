"""Constants for the Kumo Cloud integration."""

DOMAIN = "kumo_cloud"

# Config entry keys
CONF_SITE_ID = "site_id"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"

# Kumo Cloud REST API
API_BASE_URL = "https://app-prod.kumocloud.com"
API_VERSION = "v3"
API_APP_VERSION = "3.2.4"
TOKEN_REFRESH_INTERVAL = 1200  # seconds (20 min)
TOKEN_EXPIRY_MARGIN = 300  # refresh this many seconds before expiry

# Kumo operationMode values (raw API; see climate.py for HVACMode mapping)
OPERATION_MODE_OFF = "off"
OPERATION_MODE_COOL = "cool"
OPERATION_MODE_HEAT = "heat"
OPERATION_MODE_DRY = "dry"
OPERATION_MODE_VENT = "vent"
OPERATION_MODE_AUTO = "auto"
OPERATION_MODE_AUTO_COOL = "autoCool"
OPERATION_MODE_AUTO_HEAT = "autoHeat"

# Coordinator polling
DEFAULT_SCAN_INTERVAL = 60

# A false connection flag may be overridden by device telemetry for at most
# five normal polling intervals. Re-reading the same cloud snapshot does not
# extend this window.
DEVICE_TELEMETRY_MAX_AGE = 5 * DEFAULT_SCAN_INTERVAL
