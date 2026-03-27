"""Message serialization/deserialization and ID correlation for the gateway protocol."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional


def generate_id() -> str:
    return str(uuid.uuid4())


def encode(message: Dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"))


def decode(text: str) -> Dict[str, Any]:
    return json.loads(text)


def auth_message(token: str) -> str:
    return encode({"type": "auth", "token": token})


def capabilities_message(
    agent_version: str,
    ha_version: str,
    protocol_version: int = 1,
    features: Optional[list[str]] = None,
    platforms: Optional[list[str]] = None,
    tracked_devices: Optional[list[str]] = None,
) -> str:
    msg: Dict[str, Any] = {
        "type": "capabilities",
        "agent_version": agent_version,
        "ha_version": ha_version,
        "protocol_version": protocol_version,
        "features": features or [],
        "platforms": platforms or [],
        "tracked_devices": tracked_devices or [],
    }
    return encode(msg)


def response_message(
    request_id: str,
    status: str = "ok",
    data: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
) -> str:
    msg: Dict[str, Any] = {
        "type": "response",
        "id": request_id,
        "status": status,
    }
    if status == "ok" and data is not None:
        msg["data"] = data
    if status == "error" and error is not None:
        msg["error"] = error
    return encode(msg)


def event_push_message(
    event_type: str,
    data: Dict[str, Any],
) -> str:
    return encode({
        "type": "event_push",
        "event_type": event_type,
        "data": data,
    })


def state_update_message(
    device_id: str,
    data: Dict[str, Any],
) -> str:
    return encode({
        "type": "state_update",
        "device_id": device_id,
        "data": data,
    })


def health_alert_message(
    alert_type: str,
    data: Dict[str, Any],
) -> str:
    return encode({
        "type": "health_alert",
        "alert_type": alert_type,
        "data": data,
    })


def entity_id_changed_message(
    device_id: str,
    old_external_id: str,
    new_external_id: str,
) -> str:
    return encode({
        "type": "entity_id_changed",
        "device_id": device_id,
        "old_external_id": old_external_id,
        "new_external_id": new_external_id,
    })


def pong_message() -> str:
    return encode({"type": "pong"})
