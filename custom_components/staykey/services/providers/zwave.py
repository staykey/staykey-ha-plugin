"""Z-Wave LockProvider.

Wraps the existing ``zwave_js`` services and the deep readback path that
uses ``zwave-js-server-python`` directly to verify a code was actually
programmed on the lock.  The implementation is unchanged from the
pre-refactor ``services/zwave.py``; only the public surface is adapted
to the protocol-agnostic types in :mod:`..lock_provider`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from ..lock_provider import CapabilityInfo, LockProvider, ProviderResult, SlotInfo

try:
    from zwave_js_server.const import CommandClass  # noqa: F401
    from zwave_js_server.util.lock import (
        get_code_slots,  # noqa: F401  # kept for callers that still import it
        get_usercode_from_node,
        get_usercodes,
    )

    HAS_ZWAVE_LIB = True
except ImportError:
    HAS_ZWAVE_LIB = False

LOGGER = logging.getLogger(__name__)


class ZwaveLockProvider:
    """LockProvider implementation for Z-Wave JS locks."""

    name: str = "zwave"

    async def set_code(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
        code: str,
    ) -> ProviderResult:
        """Set a code and verify by actively querying the slot from the lock."""
        return await _set_and_verify_code(hass, entity_id, slot, str(code))

    async def clear_code(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
    ) -> ProviderResult:
        """Clear a code slot via ``zwave_js.clear_lock_usercode``."""
        await hass.services.async_call(
            "zwave_js",
            "clear_lock_usercode",
            {"entity_id": entity_id, "code_slot": slot},
            blocking=True,
        )
        return ProviderResult(slot=slot, method="zwave_clear", verified=True)

    async def read_codes(
        self,
        hass: HomeAssistant,
        entity_id: str,
        max_slots: int = 30,
    ) -> List[SlotInfo]:
        """Read lock code-slot contents from the cached Z-Wave ValueDB."""
        return await _read_code_slots(hass, entity_id, max_slots)

    async def get_capabilities(
        self,
        hass: HomeAssistant,
        entity_id: str,
    ) -> CapabilityInfo:
        info = await get_node_info(hass, entity_id)
        if not info:
            return CapabilityInfo(supports_access_codes=False)

        return CapabilityInfo(
            supports_access_codes=bool(info.get("supports_user_codes")),
            max_slots=info.get("max_code_slots"),
            extra=info,
        )


# ---------------------------------------------------------------------------
# Internals (private to this provider)
# ---------------------------------------------------------------------------


def _get_zwave_node_for_entity(
    hass: HomeAssistant, entity_id: str
) -> Optional[Any]:
    """Look up the Z-Wave node associated with a HA entity via the device registry."""
    try:
        entity_reg = er.async_get(hass)
        entity_entry = entity_reg.async_get(entity_id)
        if not entity_entry or not entity_entry.device_id:
            LOGGER.warning(
                "Z-Wave lookup: no entity registry entry or device_id for %s",
                entity_id,
            )
            return None

        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(entity_entry.device_id)
        if not device:
            LOGGER.warning(
                "Z-Wave lookup: no device registry entry for device_id %s",
                entity_entry.device_id,
            )
            return None

        zwave_node_id = None
        for domain, identifier in device.identifiers:
            if domain == "zwave_js":
                parts = identifier.split("-")
                if len(parts) >= 2:
                    try:
                        zwave_node_id = int(parts[1])
                    except ValueError:
                        LOGGER.warning(
                            "Z-Wave lookup: could not parse node_id from %s",
                            identifier,
                        )
                break

        if zwave_node_id is None:
            return None

        entries = hass.config_entries.async_entries("zwave_js")
        for config_entry in entries:
            state_name = (
                config_entry.state.name
                if hasattr(config_entry.state, "name")
                else str(config_entry.state)
            )
            if state_name.lower() != "loaded":
                continue
            runtime_data = getattr(config_entry, "runtime_data", None)
            if runtime_data is None:
                continue

            client = getattr(runtime_data, "client", None)
            if client is None and isinstance(runtime_data, dict):
                client = runtime_data.get("client")
            if client is None and hasattr(runtime_data, "driver"):
                client = runtime_data
            if client is None:
                continue

            driver = getattr(client, "driver", None)
            if driver is None:
                continue

            controller = getattr(driver, "controller", None)
            if controller is None:
                continue

            nodes = getattr(controller, "nodes", {})
            if zwave_node_id in nodes:
                return nodes[zwave_node_id]
            for key in nodes:
                if str(key) == str(zwave_node_id):
                    return nodes[key]

        LOGGER.warning(
            "Z-Wave lookup: node %d not found in any loaded driver", zwave_node_id
        )
    except Exception:
        LOGGER.exception("Z-Wave lookup failed for entity %s", entity_id)
    return None


async def _read_code_slots(
    hass: HomeAssistant, entity_id: str, max_slots: int = 30
) -> List[SlotInfo]:
    if not HAS_ZWAVE_LIB:
        LOGGER.warning("zwave_js_server library not available for code slot reading")
        return []

    node = _get_zwave_node_for_entity(hass, entity_id)
    if not node:
        LOGGER.warning("No Z-Wave node found for %s", entity_id)
        return []

    try:
        lib_slots = get_usercodes(node)
        results: List[SlotInfo] = []
        for s in lib_slots:
            slot_num = s["code_slot"]
            if slot_num > max_slots:
                continue
            occupied = s.get("in_use") is True
            code: Optional[str] = None
            if occupied and s.get("usercode"):
                code = str(s["usercode"])
            results.append(SlotInfo(slot=slot_num, occupied=occupied, code=code))
        return results
    except Exception:
        LOGGER.exception("Error reading code slots for %s", entity_id)
        return []


async def _fetch_code_slot(
    hass: HomeAssistant, entity_id: str, slot: int
) -> Optional[Dict[str, Any]]:
    """Actively query a single code slot from the lock over Z-Wave."""
    if not HAS_ZWAVE_LIB:
        return None

    node = _get_zwave_node_for_entity(hass, entity_id)
    if not node:
        return None

    try:
        result = await get_usercode_from_node(node, slot)
        return {
            "slot": result["code_slot"],
            "occupied": result.get("in_use") is True,
            "code": str(result["usercode"]) if result.get("usercode") else None,
        }
    except Exception:
        LOGGER.exception("Error fetching code slot %d for %s", slot, entity_id)
        return None


async def get_node_info(
    hass: HomeAssistant, entity_id: str
) -> Optional[Dict[str, Any]]:
    """Get Z-Wave node information for capability discovery.

    Public helper used by ``handlers/diagnostics.py`` and the legacy
    capability fallback in ``handlers/capability.py``; returns ``None``
    on Matter / Wi-Fi / Zigbee devices, which is fine.
    """
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
                    command_classes.append({"id": cc, "name": str(cc_name)})

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


async def _set_and_verify_code(
    hass: HomeAssistant,
    entity_id: str,
    slot: int,
    code: str,
    max_retries: int = 2,
    verify_delay_s: float = 2.0,
) -> ProviderResult:
    """Set a lock code and verify by actively querying the slot from the lock.

    Uses node.async_invoke_cc_api to fetch the slot value directly from the
    lock rather than relying on the passive ValueDB cache.
    """
    last_error: Optional[str] = None

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

            result = await _fetch_code_slot(hass, entity_id, slot)
            if (
                result
                and result.get("occupied")
                and result.get("code") == str(code)
            ):
                return ProviderResult(
                    slot=slot,
                    method="zwave_set_and_verify",
                    verified=True,
                    attempts=attempt + 1,
                )
            elif result:
                # NEVER include raw PINs in error strings — they can appear
                # in activity history and aggregated logs. Compare lengths only.
                actual = result.get("code")
                expected_len = len(str(code))
                actual_len = len(str(actual)) if actual is not None else 0
                last_error = (
                    f"Slot {slot} readback mismatch (expected_length={expected_len}, "
                    f"actual_length={actual_len})"
                )
            else:
                last_error = f"Slot {slot} could not be read from lock"

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

    return ProviderResult(
        slot=slot,
        method="zwave_set_and_verify",
        verified=False,
        attempts=max_retries + 1,
        error=last_error,
    )
