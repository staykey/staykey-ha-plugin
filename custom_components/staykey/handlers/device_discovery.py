"""Device discovery handler.

Enumerates HA entities with rich metadata using in-process registry access.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

LOGGER = logging.getLogger(__name__)

SUPPORTED_DOMAINS = {"lock", "climate", "light", "cover", "sensor", "switch"}


async def handle_discover_devices(
    hass: HomeAssistant,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Return every supported entity as a flat list, enriched with device metadata.

    Each row is one entity (not one physical device).  A ratgdo garage door
    opener will produce separate cover, light, and lock rows so the user can
    pick exactly which entities to manage.
    """
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)

    filter_domains = set(params.get("domains", [])) or SUPPORTED_DOMAINS

    # Cache device lookups so we only hit the registry once per device_id.
    device_cache: Dict[str, Any] = {}
    entities_out: List[Dict[str, Any]] = []

    for entry in entity_reg.entities.values():
        if entry.domain not in filter_domains:
            continue
        if entry.disabled:
            continue

        entity_info: Dict[str, Any] = {
            "external_id": entry.entity_id,
            "name": entry.name or entry.original_name or entry.entity_id,
            "type": entry.domain,
            "unique_id": entry.unique_id,
        }

        if entry.device_id:
            if entry.device_id not in device_cache:
                device_cache[entry.device_id] = device_reg.async_get(entry.device_id)
            device = device_cache[entry.device_id]

            if device:
                entity_info["name"] = (
                    entry.name
                    or entry.original_name
                    or device.name_by_user
                    or device.name
                    or entry.entity_id
                )
                entity_info["manufacturer"] = device.manufacturer
                entity_info["model"] = device.model
                entity_info["area_id"] = device.area_id

                protocol = _infer_protocol(device)
                if protocol:
                    entity_info["protocol"] = protocol

        state = hass.states.get(entry.entity_id)
        if state and state.attributes:
            entity_info["capabilities"] = _extract_capabilities(
                entry.domain, state.attributes
            )

        entities_out.append(entity_info)

    return {"devices": entities_out}


def _infer_protocol(device: Any) -> str | None:
    if not device.identifiers:
        return None
    for ident in device.identifiers:
        parts = list(ident)
        if len(parts) >= 1:
            domain = str(parts[0]).lower()
            if "zwave" in domain:
                return "zwave"
            if "zigbee" in domain or "zha" in domain:
                return "zigbee"
            if "bluetooth" in domain or "ble" in domain:
                return "bluetooth"
            if "matter" in domain:
                return "matter"
    return None


def _extract_capabilities(domain: str, attributes: dict) -> list[str]:
    caps: list[str] = []
    if domain == "lock":
        caps.extend(["lock", "unlock"])
        if "supported_features" in attributes:
            features = attributes["supported_features"]
            if isinstance(features, int) and features & 1:
                caps.append("access_codes")
    elif domain == "climate":
        caps.extend(["set_temperature", "set_hvac_mode"])
    elif domain == "light":
        caps.append("toggle")
    elif domain == "cover":
        caps.extend(["open", "close"])
    elif domain == "switch":
        caps.append("toggle")
    return caps

