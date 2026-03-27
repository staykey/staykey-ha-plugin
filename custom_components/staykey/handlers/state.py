"""State reading handler.

Translates Staykey get_state request to local HA state lookup.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap

LOGGER = logging.getLogger(__name__)


async def handle_get_state(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    state = hass.states.get(entity_id)
    if not state:
        return {"state": "unavailable", "attributes": {}}

    attrs = dict(state.attributes) if state.attributes else {}
    safe_attrs: Dict[str, Any] = {}
    for key, value in attrs.items():
        try:
            safe_attrs[key] = _make_serializable(value)
        except (TypeError, ValueError):
            safe_attrs[key] = str(value)

    return {
        "state": state.state,
        "attributes": safe_attrs,
        "last_changed": state.last_changed.isoformat() if state.last_changed else None,
        "last_updated": state.last_updated.isoformat() if state.last_updated else None,
    }


def _make_serializable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_make_serializable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _make_serializable(v) for k, v in value.items()}
    return str(value)
