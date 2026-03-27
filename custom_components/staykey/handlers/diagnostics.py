"""Diagnostics and health monitoring handler."""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap
from ..services import zwave

LOGGER = logging.getLogger(__name__)


async def handle_get_diagnostics(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Get diagnostic information for a device."""
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    diagnostics: Dict[str, Any] = {"device_id": device_id}

    state = hass.states.get(entity_id)
    if state:
        diagnostics["state"] = state.state
        attrs = state.attributes or {}
        if "battery_level" in attrs:
            diagnostics["battery_level"] = attrs["battery_level"]
        diagnostics["last_changed"] = (
            state.last_changed.isoformat() if state.last_changed else None
        )
        diagnostics["last_updated"] = (
            state.last_updated.isoformat() if state.last_updated else None
        )

    node_info = await zwave.get_node_info(hass, entity_id)
    if node_info:
        diagnostics["zwave_status"] = node_info.get("status", "unknown")
        diagnostics["zwave_ready"] = node_info.get("ready", False)
        diagnostics["interview_stage"] = node_info.get("interview_stage")
        if stats := node_info.get("statistics"):
            diagnostics["commands_tx"] = stats.get("commands_tx", 0)
            diagnostics["commands_rx"] = stats.get("commands_rx", 0)
            diagnostics["commands_dropped_tx"] = stats.get("commands_dropped_tx", 0)
            diagnostics["commands_dropped_rx"] = stats.get("commands_dropped_rx", 0)
            diagnostics["last_seen"] = stats.get("last_seen")

    return diagnostics
