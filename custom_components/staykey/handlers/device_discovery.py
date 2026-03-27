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
    """Discover devices on this HA instance with full metadata."""
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)

    filter_domains = set(params.get("domains", [])) or SUPPORTED_DOMAINS

    devices_out: List[Dict[str, Any]] = []
    seen_device_ids: set[str] = set()

    for entry in entity_reg.entities.values():
        if entry.domain not in filter_domains:
            continue
        if entry.disabled:
            continue

        device_info: Dict[str, Any] = {
            "external_id": entry.entity_id,
            "name": entry.name or entry.original_name or entry.entity_id,
            "type": entry.domain,
            "unique_id": entry.unique_id,
        }

        if entry.device_id and entry.device_id not in seen_device_ids:
            device = device_reg.async_get(entry.device_id)
            if device:
                seen_device_ids.add(entry.device_id)
                device_info.update({
                    "name": device.name_by_user or device.name or device_info["name"],
                    "manufacturer": device.manufacturer,
                    "model": device.model,
                    "hw_version": device.hw_version,
                    "sw_version": device.sw_version,
                    "area_id": device.area_id,
                    "device_identifiers": [
                        list(ident) for ident in device.identifiers
                    ] if device.identifiers else [],
                })

                platform = _infer_protocol(device)
                if platform:
                    device_info["protocol"] = platform

                entities = er.async_entries_for_device(
                    entity_reg, entry.device_id, include_disabled_entities=False
                )
                device_info["entities"] = [
                    {
                        "entity_id": e.entity_id,
                        "domain": e.domain,
                        "name": e.name or e.original_name or e.entity_id,
                        "unique_id": e.unique_id,
                    }
                    for e in entities
                    if e.domain in SUPPORTED_DOMAINS
                ]

                state = hass.states.get(entry.entity_id)
                if state and state.attributes:
                    device_info["capabilities"] = _extract_capabilities(
                        entry.domain, state.attributes
                    )
                    if "battery_level" in state.attributes:
                        device_info.setdefault("metadata", {})["battery_level"] = (
                            state.attributes["battery_level"]
                        )
        elif entry.device_id and entry.device_id in seen_device_ids:
            continue

        devices_out.append(device_info)

    return {"devices": devices_out}


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
