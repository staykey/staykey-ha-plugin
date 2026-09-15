"""Builders for the event payloads forwarded over the gateway.

Kept free of Home Assistant imports so they can be unit-tested with plain
objects. The plugin forwards what the hub reported and lets the receiving
side classify it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

ZWAVE_PROTOCOL = "zwave"


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def device_event_payload(event: Any, device_id: str) -> dict[str, Any]:
    """Build a ``device_event`` payload from a Z-Wave JS bus event.

    ``event`` is a ``homeassistant.core.Event`` (or anything with
    ``event_type``, ``data`` and ``time_fired``). The raw notification
    ``type`` and ``event`` numbers are passed through untouched.
    """
    data = event.data or {}
    return {
        "event_id": str(uuid.uuid4()),
        "device_id": device_id,
        "protocol": ZWAVE_PROTOCOL,
        "source_event": event.event_type,
        "notification_type": data.get("type"),
        "code": data.get("event"),
        "label": data.get("event_label"),
        "params": data.get("parameters") or {},
        "timestamp": _iso_utc(getattr(event, "time_fired", None)),
    }


def state_update_payload(new_state: Any) -> dict[str, Any]:
    """Build a ``state_update`` payload from a Home Assistant ``State``.

    The state's context id identifies this particular change, so the
    receiving side can ignore a repeated delivery.
    """
    attrs = new_state.attributes or {}
    context = getattr(new_state, "context", None)
    payload: dict[str, Any] = {
        "state": new_state.state,
        "last_changed": (
            new_state.last_changed.isoformat() if new_state.last_changed else None
        ),
        "event_id": getattr(context, "id", None) if context is not None else None,
    }
    if "battery_level" in attrs:
        payload["battery_level"] = attrs["battery_level"]
    return payload
