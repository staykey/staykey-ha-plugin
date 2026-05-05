"""Device capability discovery handler.

For lock entities, capability is sourced from the protocol-specific
:class:`LockProvider <..services.lock_provider.LockProvider>` so Matter
locks (HA 2026.4 lock manager) report PIN support correctly.  For other
domains we still expose Z-Wave node info when present, but without
hard-failing on Matter / Wi-Fi / Zigbee devices.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap
from ..services import providers
from ..services.lock_provider import UnsupportedProtocolError
from ..services.providers import zwave as zwave_provider

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

    if entity_id.startswith("lock."):
        try:
            provider = providers.select_provider(hass, entity_id)
            caps = await provider.get_capabilities(hass, entity_id)
            capabilities["protocol"] = provider.name
            capabilities["supports_access_codes"] = caps.supports_access_codes
            if caps.max_slots is not None:
                capabilities["max_slots"] = caps.max_slots
            if caps.extra:
                capabilities[provider.name] = caps.extra
            return capabilities
        except UnsupportedProtocolError:
            LOGGER.debug(
                "No LockProvider for %s; falling back to legacy zwave info",
                entity_id,
            )

    # Non-lock domains, or locks on integrations we don't have a provider
    # for: best-effort Z-Wave node lookup (returns None on Matter / Wi-Fi
    # / Zigbee devices, which is fine).
    node_info = await zwave_provider.get_node_info(hass, entity_id)
    if node_info:
        capabilities["zwave"] = node_info

    return capabilities
