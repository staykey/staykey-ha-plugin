"""Z-Wave operations via HA's internal zwave_js integration.

Accesses Z-Wave JS server through HA's zwave_js config entry runtime data
to perform operations not exposed by the REST API (code slot reading,
node interview status, etc).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

LOGGER = logging.getLogger(__name__)


def _get_zwave_client(hass: HomeAssistant) -> Optional[Any]:
    """Retrieve the zwave_js client from HA's integration runtime data."""
    try:
        entries = hass.config_entries.async_entries("zwave_js")
        for entry in entries:
            if entry.state.name == "loaded":
                runtime_data = getattr(entry, "runtime_data", None)
                if runtime_data is None:
                    continue
                client = getattr(runtime_data, "client", None)
                if client:
                    return client

                if isinstance(runtime_data, dict):
                    return runtime_data.get("client")
    except Exception:
        LOGGER.debug("Could not access zwave_js runtime data", exc_info=True)
    return None


def _get_zwave_node_for_entity(hass: HomeAssistant, entity_id: str) -> Optional[Any]:
    """Look up the Z-Wave node associated with a HA entity."""
    try:
        entity_reg = er.async_get(hass)
        entry = entity_reg.async_get(entity_id)
        if not entry or not entry.device_id:
            return None

        entries = hass.config_entries.async_entries("zwave_js")
        for config_entry in entries:
            if config_entry.state.name != "loaded":
                continue
            runtime_data = getattr(config_entry, "runtime_data", None)
            if runtime_data is None:
                continue

            client = getattr(runtime_data, "client", None)
            if client is None and isinstance(runtime_data, dict):
                client = runtime_data.get("client")

            if client is None:
                continue

            driver = getattr(client, "driver", None)
            if driver is None:
                continue

            controller = getattr(driver, "controller", None)
            if controller is None:
                continue

            nodes = getattr(controller, "nodes", {})
            for node in nodes.values():
                device_id = getattr(node, "device_id", None)
                if device_id and str(device_id) == entry.device_id:
                    return node

                endpoints = getattr(node, "endpoints", {})
                for endpoint in endpoints.values():
                    values = getattr(endpoint, "values", {})
                    for value in values.values():
                        value_id = getattr(value, "value_id", None)
                        if value_id and entry.unique_id and str(value_id) in entry.unique_id:
                            return node

    except Exception:
        LOGGER.debug("Could not find Z-Wave node for entity %s", entity_id, exc_info=True)
    return None


async def read_code_slots(
    hass: HomeAssistant, entity_id: str, max_slots: int = 30
) -> List[Dict[str, Any]]:
    """Read lock code slot contents from the Z-Wave node.

    Uses the Z-Wave JS UserCode command class to read actual slot data.
    """
    node = _get_zwave_node_for_entity(hass, entity_id)
    if not node:
        LOGGER.warning("No Z-Wave node found for %s", entity_id)
        return []

    slots: List[Dict[str, Any]] = []

    try:
        endpoints = getattr(node, "endpoints", {})
        for endpoint in endpoints.values():
            values = getattr(endpoint, "values", {})
            for value in values.values():
                cc = getattr(value, "command_class", None)
                cc_name = getattr(value, "command_class_name", "")

                if cc == 99 or "User Code" in str(cc_name) or "UserCode" in str(cc_name):
                    property_name = getattr(value, "property_name", "")
                    property_key = getattr(value, "property_key", None)

                    if property_name == "userCode" and property_key is not None:
                        slot_num = property_key
                        if isinstance(slot_num, int) and slot_num <= max_slots:
                            raw_value = getattr(value, "value", None)
                            status_value_id = f"{value.value_id}".replace(
                                "userCode", "userIdStatus"
                            )
                            status = None
                            for sv in values.values():
                                if (
                                    getattr(sv, "property_name", "") == "userIdStatus"
                                    and getattr(sv, "property_key", None) == slot_num
                                ):
                                    status = getattr(sv, "value", None)
                                    break

                            occupied = status == 1 if status is not None else (raw_value is not None and raw_value != "")

                            slot_info: Dict[str, Any] = {
                                "slot": slot_num,
                                "occupied": occupied,
                            }
                            if occupied and raw_value:
                                slot_info["code"] = str(raw_value)
                            slots.append(slot_info)
    except Exception:
        LOGGER.exception("Error reading code slots for %s", entity_id)

    slots.sort(key=lambda s: s["slot"])
    return slots


async def get_node_info(
    hass: HomeAssistant, entity_id: str
) -> Optional[Dict[str, Any]]:
    """Get Z-Wave node information for capability discovery."""
    node = _get_zwave_node_for_entity(hass, entity_id)
    if not node:
        return None

    try:
        info: Dict[str, Any] = {
            "node_id": getattr(node, "node_id", None),
            "status": str(getattr(node, "status", "unknown")),
            "ready": getattr(node, "ready", False),
            "interview_stage": str(getattr(node, "interview_stage", "unknown")),
        }

        command_classes = []
        endpoints = getattr(node, "endpoints", {})
        for endpoint in endpoints.values():
            values = getattr(endpoint, "values", {})
            seen_ccs: set = set()
            for value in values.values():
                cc = getattr(value, "command_class", None)
                cc_name = getattr(value, "command_class_name", "")
                if cc is not None and cc not in seen_ccs:
                    seen_ccs.add(cc)
                    command_classes.append({
                        "id": cc,
                        "name": str(cc_name),
                    })

        info["command_classes"] = command_classes

        if any(cc["id"] == 99 for cc in command_classes):
            info["supports_user_codes"] = True
            for endpoint in endpoints.values():
                values = getattr(endpoint, "values", {})
                for value in values.values():
                    if (
                        getattr(value, "command_class", None) == 99
                        and getattr(value, "property_name", "") == "userCode"
                    ):
                        max_slot = max(
                            (
                                getattr(v, "property_key", 0)
                                for v in values.values()
                                if getattr(v, "command_class", None) == 99
                                and getattr(v, "property_name", "") == "userCode"
                                and isinstance(getattr(v, "property_key", None), int)
                            ),
                            default=0,
                        )
                        info["max_code_slots"] = max_slot
                        break

        stats = getattr(node, "statistics", None)
        if stats:
            info["statistics"] = {
                "commands_tx": getattr(stats, "commands_tx", 0),
                "commands_rx": getattr(stats, "commands_rx", 0),
                "commands_dropped_tx": getattr(stats, "commands_dropped_tx", 0),
                "commands_dropped_rx": getattr(stats, "commands_dropped_rx", 0),
                "last_seen": str(getattr(stats, "last_seen", "")),
            }

        return info

    except Exception:
        LOGGER.exception("Error getting node info for %s", entity_id)
        return None


async def set_and_verify_code(
    hass: HomeAssistant,
    entity_id: str,
    slot: int,
    code: str,
    max_retries: int = 2,
    verify_delay_s: float = 3.0,
) -> Dict[str, Any]:
    """Set a lock code and verify by reading back the slot.

    Handles unreliable Z-Wave locks by performing set-then-verify as an atomic operation.
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            await hass.services.async_call(
                "zwave_js",
                "set_lock_usercode",
                {
                    "entity_id": entity_id,
                    "code_slot": slot,
                    "usercode": str(code),
                },
                blocking=True,
            )

            await asyncio.sleep(verify_delay_s)

            slots = await read_code_slots(hass, entity_id, max_slots=slot + 1)
            for s in slots:
                if s["slot"] == slot:
                    if s.get("occupied") and s.get("code") == str(code):
                        return {
                            "slot": slot,
                            "verified": True,
                            "method": "zwave_set_and_verify",
                            "attempts": attempt + 1,
                        }
                    else:
                        last_error = f"Slot {slot} readback mismatch: expected {code}, got {s.get('code')}"
                        break
            else:
                last_error = f"Slot {slot} not found in readback"

        except Exception as exc:
            last_error = str(exc)
            LOGGER.warning(
                "Code set attempt %d/%d failed for %s slot %d: %s",
                attempt + 1,
                max_retries + 1,
                entity_id,
                slot,
                exc,
            )

        if attempt < max_retries:
            await asyncio.sleep(1.0)

    return {
        "slot": slot,
        "verified": False,
        "method": "zwave_set_and_verify",
        "attempts": max_retries + 1,
        "error": last_error,
    }
