"""Constants for the StayKey integration."""

from __future__ import annotations

DOMAIN: str = "staykey"

# Config entry keys
CONF_INTEGRATION_ID: str = "integration_id"
CONF_BACKEND_URL: str = "backend_url"
CONF_SIGNING_SECRET: str = "signing_secret"

# Options
CONF_FORWARD_ALL_NOTIFICATIONS: str = "forward_all_notifications"
CONF_VERIFY_SSL: str = "verify_ssl"
CONF_TIMEOUT: str = "timeout"
CONF_ENDPOINT_PATH: str = "endpoint_path"

DEFAULT_TIMEOUT_SECONDS: int = 10
DEFAULT_VERIFY_SSL: bool = True
DEFAULT_FORWARD_ALL_NOTIFICATIONS: bool = False
DEFAULT_ENDPOINT_PATH: str = "/ha/webhooks/events"

# Headers
HDR_STAYKEY_ID: str = "X-StayKey-Id"
HDR_STAYKEY_SIGNATURE: str = "X-StayKey-Signature"
HDR_STAYKEY_TIMESTAMP: str = "X-StayKey-Timestamp"

# Event types we care about primarily
ZWAVE_NOTIFICATION_EVENT: str = "zwave_js_notification"
ZWAVE_VALUE_NOTIFICATION_EVENT: str = "zwave_js_value_notification"
ZWAVE_VALUE_UPDATED_EVENT: str = "zwave_js_value_updated"


