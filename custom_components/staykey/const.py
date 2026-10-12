"""Constants for the Staykey integration."""

from __future__ import annotations

DOMAIN: str = "staykey"

# Config entry keys
CONF_GATEWAY_TOKEN: str = "gateway_token"
CONF_GATEWAY_URL: str = "gateway_url"

# Options
CONF_ALLOW_SUPPORT_ACTIONS: str = "allow_support_actions"

DEFAULT_ALLOW_SUPPORT_ACTIONS: bool = False
DEFAULT_GATEWAY_URL: str = "wss://api.staykey.co/orion/gateway/websocket"

# Event types we care about primarily
ZWAVE_NOTIFICATION_EVENT: str = "zwave_js_notification"
ZWAVE_VALUE_NOTIFICATION_EVENT: str = "zwave_js_value_notification"

# Features advertised to the gateway in the capabilities handshake.
GATEWAY_FEATURES: tuple[str, ...] = (
    "lock_control",
    "access_code_management",
    "zwave_code_slots",
    "state_streaming",
    "device_discovery",
    "capability_discovery",
    "health_monitoring",
    "diagnostics",
    "batch_operations",
    "raw_device_events",
)
