"""Z-Wave LockProvider.

Works only through the public ``zwave_js`` services and the entities the
Z-Wave JS integration exposes; it never touches the integration's driver
or nodes directly.

* ``set_lock_usercode`` / ``clear_lock_usercode`` write and clear codes.
* ``get_lock_usercode`` reads code slots from Z-Wave JS's cache.
* ``invoke_cc_api`` (User Code CC ``get``) asks the lock to report a slot,
  which is how a write is verified.
* The node status and statistics sensors feed diagnostics.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..lock_provider import CapabilityInfo, ProviderResult, SlotInfo

LOGGER = logging.getLogger(__name__)

_ZWAVE_DOMAIN = "zwave_js"

# Z-Wave User Code Command Class.
_USER_CODE_CC = 99

# Pause between asking the lock to report a slot and reading the slot back,
# so the lock's Report has reached the cache that ``get_lock_usercode`` reads.
_READBACK_SETTLE_S = 0.5

# Node statistics sensors the Z-Wave JS integration creates for each node
# (disabled by default; read only when the user has enabled them).
_STATISTICS_KEYS = (
    "commands_tx",
    "commands_rx",
    "commands_dropped_tx",
    "commands_dropped_rx",
    "last_seen",
)


@dataclass(frozen=True)
class _SlotState:
    """A code slot as Z-Wave JS last heard it from the lock.

    ``code`` is whatever the lock reported, even for a slot that is not in
    use, so two reads of an unchanged slot compare equal.
    """

    occupied: bool
    code: str | None


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
        """Set a code, then ask the lock to report the slot to verify it."""
        return await _set_and_verify_code(hass, entity_id, slot, str(code))

    async def clear_code(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
    ) -> ProviderResult:
        """Clear a code slot via ``zwave_js.clear_lock_usercode``."""
        await hass.services.async_call(
            _ZWAVE_DOMAIN,
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
    ) -> list[SlotInfo]:
        """Read lock code-slot contents via ``zwave_js.get_lock_usercode``."""
        slots = await _get_usercodes(hass, entity_id)
        if not slots:
            return []
        return [
            SlotInfo(
                slot=slot_num,
                occupied=state.occupied,
                code=state.code if state.occupied else None,
            )
            for slot_num, state in sorted(slots.items())
            if slot_num <= max_slots
        ]

    async def get_capabilities(
        self,
        hass: HomeAssistant,
        entity_id: str,
    ) -> CapabilityInfo:
        info = await get_node_info(hass, entity_id)
        slots = await _get_usercodes(hass, entity_id)
        if not slots:
            return CapabilityInfo(supports_access_codes=False, extra=info or {})

        max_slots = max(slots)
        extra = dict(info or {})
        extra["supports_user_codes"] = True
        extra["max_code_slots"] = max_slots
        return CapabilityInfo(
            supports_access_codes=True,
            max_slots=max_slots,
            extra=extra,
        )


# ---------------------------------------------------------------------------
# Internals (private to this provider)
# ---------------------------------------------------------------------------


def _parse_slot(raw: Any) -> _SlotState | None:
    if not isinstance(raw, dict):
        return None
    code = raw.get("usercode")
    return _SlotState(
        occupied=raw.get("in_use") is True,
        code=str(code) if code else None,
    )


async def _get_usercodes(
    hass: HomeAssistant, entity_id: str, slot: int | None = None
) -> dict[int, _SlotState] | None:
    """Read code slots with ``zwave_js.get_lock_usercode``.

    Reads Z-Wave JS's cache, which only changes when the lock reports a
    slot: Z-Wave JS does not record a code as set until the lock says so.
    Returns ``None`` when the service call fails, for example when the
    slot has never been read from the lock.
    """
    data: dict[str, Any] = {"entity_id": entity_id}
    if slot is not None:
        data["code_slot"] = slot
    try:
        response = await hass.services.async_call(
            _ZWAVE_DOMAIN,
            "get_lock_usercode",
            data,
            blocking=True,
            return_response=True,
        )
    except Exception as exc:
        LOGGER.warning(
            "zwave_js.get_lock_usercode failed for %s (slot %s): %s",
            entity_id,
            slot if slot is not None else "all",
            exc,
        )
        return None

    if not isinstance(response, dict):
        return None
    slots = response.get(entity_id, response)
    if not isinstance(slots, dict):
        return None

    results: dict[int, _SlotState] = {}
    for key, raw in slots.items():
        try:
            slot_num = int(key)
        except (TypeError, ValueError):
            continue
        state = _parse_slot(raw)
        if state is not None:
            results[slot_num] = state
    return results


async def _read_slot(
    hass: HomeAssistant, entity_id: str, slot: int
) -> _SlotState | None:
    slots = await _get_usercodes(hass, entity_id, slot)
    if not slots:
        return None
    return slots.get(slot)


async def _request_slot_report(hass: HomeAssistant, entity_id: str, slot: int) -> bool:
    """Ask the lock to report *slot* with ``zwave_js.invoke_cc_api``.

    The service waits for the lock's Report unless the node is asleep or
    dead.  Returns ``False`` if the request could not be sent.
    """
    try:
        await hass.services.async_call(
            _ZWAVE_DOMAIN,
            "invoke_cc_api",
            {
                "entity_id": entity_id,
                "command_class": _USER_CODE_CC,
                "method_name": "get",
                "parameters": [slot],
            },
            blocking=True,
        )
    except Exception as exc:
        LOGGER.warning(
            "zwave_js.invoke_cc_api User Code get failed for %s slot %d: %s",
            entity_id,
            slot,
            exc,
        )
        return False
    return True


def _get_zwave_device(
    hass: HomeAssistant, entity_id: str
) -> tuple[Any, int | None] | None:
    """Return the entity's device and its Z-Wave node ID, from the registries."""
    entity_reg = er.async_get(hass)
    entity_entry = entity_reg.async_get(entity_id)
    if not entity_entry or not entity_entry.device_id:
        return None

    device = dr.async_get(hass).async_get(entity_entry.device_id)
    if not device:
        return None

    for domain, identifier in device.identifiers:
        if domain != _ZWAVE_DOMAIN:
            continue
        # Z-Wave JS device identifiers are "<home_id>-<node_id>[-...]".
        parts = identifier.split("-")
        try:
            node_id = int(parts[1]) if len(parts) >= 2 else None
        except ValueError:
            node_id = None
        return device, node_id

    return None


def _sensor_value(raw: str) -> Any:
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return raw
    return int(number) if number.is_integer() else number


async def get_node_info(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    """Describe the Z-Wave node behind *entity_id* for capability and diagnostics.

    Built from the device registry and the node status and statistics
    sensors Z-Wave JS creates.  Returns ``None`` when the entity is not on a
    Z-Wave JS device (Matter / Wi-Fi / Zigbee), which callers treat as "no
    Z-Wave details".
    """
    try:
        found = _get_zwave_device(hass, entity_id)
        if found is None:
            return None
        device, node_id = found

        info: dict[str, Any] = {
            "node_id": node_id,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "firmware": device.sw_version,
        }

        statistics: dict[str, Any] = {}
        entity_reg = er.async_get(hass)
        for entry in er.async_entries_for_device(entity_reg, device.id):
            if entry.platform != _ZWAVE_DOMAIN or not entry.unique_id:
                continue
            state = hass.states.get(entry.entity_id)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            if entry.unique_id.endswith(".node_status"):
                info["status"] = state.state
                continue
            for key in _STATISTICS_KEYS:
                if entry.unique_id.endswith(f".statistics_{key}"):
                    statistics[key] = _sensor_value(state.state)
                    break

        if statistics:
            info["statistics"] = statistics
        return info
    except Exception:
        LOGGER.exception("Error getting Z-Wave node info for %s", entity_id)
        return None


async def _set_and_verify_code(
    hass: HomeAssistant,
    entity_id: str,
    slot: int,
    code: str,
    max_retries: int = 2,
    verify_delay_s: float = 2.0,
) -> ProviderResult:
    """Set a lock code and verify it by asking the lock to report the slot.

    Z-Wave JS only changes a code slot in its cache when the lock reports
    it, so the slot is read before the write and again after asking the
    lock for a Report.  A slot that holds the new code was confirmed by the
    lock.  A slot that is unchanged means the lock either did not answer or
    still holds what it held before; the two look the same from the public
    services, so both are reported as "not confirmed".  (If the slot
    already held the new code before the write, it reads as verified: the
    lock reported that code earlier.)
    """
    last_error: str | None = None

    # Read once, before the first write: on a retry, a lock that keeps
    # reporting the same wrong code must still read as a mismatch.
    try:
        before = await _read_slot(hass, entity_id, slot)
    except Exception:
        before = None

    for attempt in range(max_retries + 1):
        try:
            await hass.services.async_call(
                _ZWAVE_DOMAIN,
                "set_lock_usercode",
                {
                    "entity_id": entity_id,
                    "code_slot": slot,
                    "usercode": str(code),
                },
                blocking=True,
            )

            await asyncio.sleep(verify_delay_s)

            requested = await _request_slot_report(hass, entity_id, slot)
            await asyncio.sleep(_READBACK_SETTLE_S)
            after = await _read_slot(hass, entity_id, slot) if requested else None

            if after is None:
                last_error = f"Slot {slot} could not be read from lock"
            elif after.occupied and after.code == str(code):
                return ProviderResult(
                    slot=slot,
                    method="zwave_set_and_verify",
                    verified=True,
                    attempts=attempt + 1,
                )
            elif after == before:
                last_error = (
                    f"Slot {slot}: lock did not confirm the code "
                    "(no response, or slot unchanged)"
                )
            else:
                # NEVER include raw PINs in error strings — they can appear
                # in activity history and aggregated logs. Compare lengths only.
                expected_len = len(str(code))
                actual_len = len(after.code) if after.code is not None else 0
                last_error = (
                    f"Slot {slot} readback mismatch (expected_length={expected_len}, "
                    f"actual_length={actual_len})"
                )

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
