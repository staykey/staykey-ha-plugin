"""Constants for the StayKey integration."""

from __future__ import annotations

DOMAIN: str = "staykey"

# Config entry keys
CONF_ENDPOINT_URL: str = "endpoint_url"

# Options
CONF_FORWARD_ALL_NOTIFICATIONS: str = "forward_all_notifications"
CONF_VERIFY_SSL: str = "verify_ssl"
CONF_TIMEOUT: str = "timeout"

DEFAULT_TIMEOUT_SECONDS: int = 10
DEFAULT_VERIFY_SSL: bool = True
DEFAULT_FORWARD_ALL_NOTIFICATIONS: bool = False
 

# Event types we care about primarily
ZWAVE_NOTIFICATION_EVENT: str = "zwave_js_notification"
ZWAVE_VALUE_NOTIFICATION_EVENT: str = "zwave_js_value_notification"
ZWAVE_VALUE_UPDATED_EVENT: str = "zwave_js_value_updated"
