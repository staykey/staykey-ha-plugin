"""Device capability discovery handler.

Inspects Z-Wave nodes for supported command classes, code slot count, etc.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap
from ..services import zwave

LOGGER = logging.getLogger(__name__)


async def handle_get_capabilities(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Get detailed capability information for a device."""
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    node_info = await zwave.get_node_info(hass, entity_id)

    capabilities: Dict[str, Any] = {
        "device_id": device_id,
        "entity_id": entity_id,
    }

    state = hass.states.get(entity_id)
    if state:
        domain = entity_id.split(".")[0]
        capabilities["domain"] = domain
        capabilities["current_state"] = state.state

        attrs = state.attributes or {}
        if "supported_features" in attrs:
            capabilities["supported_features"] = attrs["supported_features"]
        if "device_class" in attrs:
            capabilities["device_class"] = attrs["device_class"]

    if node_info:
        capabilities["zwave"] = node_info

    return capabilities
