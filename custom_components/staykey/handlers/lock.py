"""Lock/unlock and access code command handlers.

Translates Staykey-owned schemas to HA service calls.  Access-code
operations are delegated to a protocol-specific
:class:`LockProvider <..services.lock_provider.LockProvider>` selected
from the device-registry identifiers behind the entity (Z-Wave, Matter, ...).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap
from ..services import providers
from ..services.lock_provider import ProviderResult, SlotInfo
from .utils import ProgressFn, wait_for_state

LOGGER = logging.getLogger(__name__)

_LOCK_STATE_TIMEOUT = 15  # seconds — matches Orion backend


async def handle_lock(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
    progress_fn: Optional[ProgressFn] = None,
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    await hass.services.async_call(
        "lock", "lock", {"entity_id": entity_id}, blocking=True
    )

    status = await wait_for_state(
        hass, entity_id, "locked", _LOCK_STATE_TIMEOUT, progress_fn=progress_fn,
    )
    return {"state": status, "method": "remote"}


async def handle_unlock(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
    progress_fn: Optional[ProgressFn] = None,
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    service_data: Dict[str, Any] = {"entity_id": entity_id}
    if code := params.get("code"):
        service_data["code"] = code

    await hass.services.async_call(
        "lock", "unlock", service_data, blocking=True
    )

    status = await wait_for_state(
        hass, entity_id, "unlocked", _LOCK_STATE_TIMEOUT, progress_fn=progress_fn,
    )
    return {"state": status, "method": "remote"}


async def handle_set_access_code(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Set an access code on a lock via the protocol-appropriate provider."""
    entity_id = _resolve_entity_id(device_map, params)
    slot, code = _resolve_slot_and_code(params)

    provider = providers.select_provider(hass, entity_id)
    result = await provider.set_code(hass, entity_id, slot, str(code))
    return _provider_result_to_dict(result)


async def handle_clear_access_code(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Clear an access code from a lock via the protocol-appropriate provider."""
    entity_id = _resolve_entity_id(device_map, params)
    slot = _resolve_slot(params)

    provider = providers.select_provider(hass, entity_id)
    result = await provider.clear_code(hass, entity_id, slot)

    out = _provider_result_to_dict(result)
    out["cleared"] = result.error is None
    return out


async def handle_read_codes(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Read code slot contents from a lock via the protocol-appropriate provider."""
    entity_id = _resolve_entity_id(device_map, params)
    max_slots = int(params.get("max_slots", 30))

    provider = providers.select_provider(hass, entity_id)
    slots = await provider.read_codes(hass, entity_id, max_slots=max_slots)
    return {"slots": [_slot_info_to_dict(s) for s in slots]}


async def handle_lock_capabilities(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Get protocol-aware capability info for a lock entity."""
    entity_id = _resolve_entity_id(device_map, params)
    provider = providers.select_provider(hass, entity_id)
    caps = await provider.get_capabilities(hass, entity_id)
    return {
        "protocol": provider.name,
        "supports_access_codes": caps.supports_access_codes,
        "max_slots": caps.max_slots,
        "extra": caps.extra,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_entity_id(device_map: DeviceMap, params: Dict[str, Any]) -> str:
    """Resolve HA entity_id from either ``external_id`` or ``device_id``.

    Orion sends ``external_id`` (the HA entity_id) for worker-driven
    actions, while dashboard actions send ``device_id`` (Staykey internal
    UUID that needs a device_map lookup).  Accept both conventions.
    """
    external_id = params.get("external_id", "")
    if external_id:
        return external_id

    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if entity_id:
        return entity_id

    raise ValueError(
        f"Cannot resolve entity: device_id={device_id!r}, external_id={external_id!r}"
    )


def _resolve_slot(params: Dict[str, Any]) -> int:
    slot = params.get("slot") or params.get("code_slot")
    if slot is None:
        raise ValueError("slot/code_slot is required")
    return int(slot)


def _resolve_slot_and_code(params: Dict[str, Any]) -> tuple[int, str]:
    code = params.get("code") or params.get("access_code")
    if not code:
        raise ValueError("code/access_code is required")
    return _resolve_slot(params), str(code)


def _provider_result_to_dict(result: ProviderResult) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "slot": result.slot,
        "method": result.method,
        "verified": result.verified,
        "attempts": result.attempts,
    }
    if result.error:
        out["error"] = result.error
    if result.extra:
        out["extra"] = result.extra
    return out


def _slot_info_to_dict(info: SlotInfo) -> Dict[str, Any]:
    out: Dict[str, Any] = {"slot": info.slot, "occupied": info.occupied}
    if info.code is not None:
        out["code"] = info.code
    return out
