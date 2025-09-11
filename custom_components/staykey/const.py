"""Constants for the StayKey integration."""

from __future__ import annotations

DOMAIN: str = "staykey"

# Config entry keys
CONF_INTEGRATION_ID: str = "integration_id"
CONF_ENDPOINT_URL: str = "endpoint_url"

# Options
CONF_FORWARD_ALL_NOTIFICATIONS: str = "forward_all_notifications"
CONF_VERIFY_SSL: str = "verify_ssl"
CONF_TIMEOUT: str = "timeout"
DEFAULT_ENDPOINT_URL: str = "https://api.staykey.example/ha/webhooks/events"

DEFAULT_TIMEOUT_SECONDS: int = 10
DEFAULT_VERIFY_SSL: bool = True
DEFAULT_FORWARD_ALL_NOTIFICATIONS: bool = False
 

# Headers
HDR_STAYKEY_ID: str = "X-StayKey-Id"
 

# Event types we care about primarily
ZWAVE_NOTIFICATION_EVENT: str = "zwave_js_notification"
ZWAVE_VALUE_NOTIFICATION_EVENT: str = "zwave_js_value_notification"
ZWAVE_VALUE_UPDATED_EVENT: str = "zwave_js_value_updated"


