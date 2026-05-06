"""Matter LockProvider (HA 2026.4 Matter Lock Manager).

Built on the new ``matter.set_lock_credential`` /
``matter.clear_lock_user`` / ``matter.get_lock_credential_status``
service actions added in Home Assistant 2026.4.

Why ``user_index = code_slot``: the Matter ``DoorLock`` cluster's
``SetCredential`` command lets the caller assign the ``userIndex``
(range 1-65534).  By making it equal to the Staykey code_slot we avoid
maintaining a separate ``slot -> matter_user_id`` mapping store, so
Orion's direct path and the plugin's gateway path stay perfectly
consistent without reconciliation.

## Why we don't call ``matter.set_lock_user``

HA's ``matter.set_lock_user`` helper is structurally hostile to
slot-based callers:

* If you pass ``user_index=N``, HA does ``GetUser(N)`` first.  If the
  slot is empty it raises ``UserSlotEmptyError("User slot N is empty")``
  (intended to prevent accidentally Adding when you meant to Modify).
* The "auto-allocate empty slot then Add" branch only runs when
  ``user_index=None``, which would force us to give up the
  ``slot == user_index`` invariant.

Per the Matter 1.x spec (DoorLock cluster, SetCredential command), when
``operationType=kAdd`` and ``userIndex`` references a non-existent
user, the lock auto-creates that user with default attributes
(``kOccupiedEnabled`` / ``kUnrestrictedUser`` / ``kSingle``).  HA's
``set_lock_credential`` helper picks Add vs Modify based on the
**credential** slot's occupancy, so a single ``set_lock_credential``
call with ``user_index=slot`` covers both the "new user + new
credential" and "update existing credential" cases for arbitrary
caller-supplied slot numbers.

## SetCredential parameter rules (and the 0x85 trap)

Matter spec ``5.2.4.40`` and connectedhomeip's validity check
(``DoorLockServer::SetCredential``, chip commit ``16657402aa``) require:

* ``OperationType=Add`` with ``userIndex`` non-null
  → ``userStatus`` and ``userType`` **MUST both be null**.
* ``OperationType=Modify`` with ``userIndex`` non-null
  → ``userStatus`` and ``userType`` **MUST both be null**.

If either field is non-null the Matter SDK rejects the command with
``DlStatus::kInvalidField``, which surfaces over the Interaction Model
as ``Status::InvalidCommand`` (``0x85`` = ``133``).  HA's
``SET_CREDENTIAL_STATUS_MAP`` only maps the four lock-level DlStatus
values (success / failure / duplicate / occupied), so 0x85 renders as
``unknown(133)``.

We therefore send **only** ``credential_type``, ``credential_data``,
``credential_index``, and ``user_index`` to ``set_lock_credential``.
The lock auto-creates the user with ``kUnrestrictedUser`` /
``kOccupiedEnabled`` defaults on Add — exactly what we want.  This
matches the spec for the entire compliant lock fleet (Aqara U200/U300,
Schlage Sense Pro, Yale Assure 2, Z-Wave locks bridged via Matter, and
the Ultraloq Bolt SE / Bolt Fingerprint).

> Earlier revisions of this provider sent ``user_type=unrestricted_user``
> believing the Bolt SE required it explicitly; that turned out to be
> the cause of the 0x85 rejection rather than the cure (the SDK
> validity check fires before the lock vendor logic gets to look at
> the command).

## Verification semantics

* ``matter.set_lock_credential`` returns its result synchronously
  (``credential_index``, ``user_index``, ``next_credential_index``) when
  called with ``return_response=True``.  A successful response means the
  Matter server programmed the credential on the lock.
* If the call raises with a HA-translated status of ``duplicate``, the
  same credential bytes are already programmed in that slot — treated
  as a verified no-op (important for Oban retries that succeeded
  silently the first time).
* On any other rejection we extract the structured Matter status from
  the exception's ``translation_placeholders`` and surface it both in
  logs and in ``ProviderResult.extra["matter_status"]`` /
  ``ProviderResult.error`` so Orion's activity log shows the actual
  Matter status code rather than just "set_lock_credential failed".
* As a defensive sanity check we fall back to
  ``matter.get_lock_credential_status`` when the set call returns
  without a usable ``credential_index`` for any other reason.

## Wide-event logging

Every ``set_code`` / ``clear_code`` attempt emits a single
``INFO``-level structured log line on completion with: ``entity_id``,
``slot``, ``method``, ``verified``, ``matter_status``,
``duration_ms``, and ``error`` (if any).  The PIN bytes are never
logged; only ``code_length`` is included so you can correlate length-
related rejections without leaking secrets.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from homeassistant.exceptions import HomeAssistantError

from ..lock_provider import CapabilityInfo, LockProvider, ProviderResult, SlotInfo

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

_MATTER_DOMAIN = "matter"
_PIN = "pin"

# DoorLock SetCredential status codes that are non-fatal for our use
# case.  Mapped from chip.clusters.DoorLock.Enums.DlStatus by HA in
# homeassistant/components/matter/lock_helpers.py:SET_CREDENTIAL_STATUS_MAP.
_DUPLICATE_STATUS = "duplicate"


class MatterLockProvider:
    """LockProvider implementation for HA 2026.4 Matter locks."""

    name: str = "matter"

    async def set_code(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
        code: str,
    ) -> ProviderResult:
        """Set a PIN credential for *slot*, auto-creating the user if needed.

        Single ``matter.set_lock_credential`` call:

        * If ``credential_index=slot`` is empty, HA sends ``kAdd``; the
          Matter spec auto-creates user ``slot`` with default attributes
          (``kOccupiedEnabled`` / ``kUnrestrictedUser`` / ``kSingle``)
          and attaches the PIN to it.
        * If the slot already holds a credential, HA sends ``kModify``
          to update the PIN bytes.
        * If the new PIN bytes match what's already there, the lock
          returns ``duplicate`` status — we treat that as success.

        We deliberately send neither ``user_status`` nor ``user_type``:
        per Matter spec ``5.2.4.40`` (and the chip SDK validity check)
        both fields MUST be null when ``userIndex`` is non-null on Add
        or Modify.  See module docstring for the full rationale.

        Falls back to ``matter.get_lock_credential_status`` only if the
        set call returns without a usable ``credential_index`` and
        didn't raise.
        """
        started_at = time.monotonic()
        request_payload: Dict[str, Any] = {
            "entity_id": entity_id,
            "credential_type": _PIN,
            "credential_data": str(code),
            "credential_index": slot,
            "user_index": slot,
        }
        LOGGER.debug(
            "matter.set_lock_credential request: entity_id=%s slot=%d "
            "code_length=%d (user_status / user_type omitted per Matter spec)",
            entity_id,
            slot,
            len(code),
        )

        set_response: Optional[Dict[str, Any]] = None
        try:
            set_response = await hass.services.async_call(
                _MATTER_DOMAIN,
                "set_lock_credential",
                request_payload,
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as exc:
            if _is_duplicate_credential_error(exc):
                result = ProviderResult(
                    slot=slot,
                    method="matter_set_credential_duplicate",
                    verified=True,
                    extra={"status": _DUPLICATE_STATUS},
                )
                _log_set_outcome(entity_id, slot, code, started_at, result)
                return result
            matter_status = _extract_matter_status(exc)
            extra: Dict[str, Any] = {}
            if matter_status is not None:
                extra["matter_status"] = matter_status
            result = ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=False,
                error=_format_set_error(matter_status, exc),
                extra=extra,
            )
            _log_set_outcome(entity_id, slot, code, started_at, result, exc=exc)
            return result
        except Exception as exc:  # pragma: no cover - belt-and-suspenders
            result = ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=False,
                error=f"set_lock_credential: {exc}",
            )
            LOGGER.exception(
                "matter.set_lock_credential raised non-HA exception for %s slot %d",
                entity_id,
                slot,
            )
            _log_set_outcome(entity_id, slot, code, started_at, result, exc=exc)
            return result

        per_entity = _extract_entity_response(set_response, entity_id)
        if per_entity and per_entity.get("credential_index") is not None:
            result = ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=True,
                extra={
                    "credential_index": per_entity.get("credential_index"),
                    "user_index": per_entity.get("user_index"),
                    "next_credential_index": per_entity.get("next_credential_index"),
                },
            )
            _log_set_outcome(entity_id, slot, code, started_at, result)
            return result

        status = await _get_credential_status(hass, entity_id, slot)
        if status and status.get("credential_exists"):
            result = ProviderResult(
                slot=slot,
                method="matter_active_read",
                verified=True,
                extra={"user_index": status.get("user_index")},
            )
            _log_set_outcome(entity_id, slot, code, started_at, result)
            return result

        result = ProviderResult(
            slot=slot,
            method="matter_set_credential",
            verified=False,
            error="set_lock_credential returned no credential_index and active read did not confirm",
        )
        _log_set_outcome(entity_id, slot, code, started_at, result)
        return result

    async def clear_code(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
    ) -> ProviderResult:
        """Clear by removing the user.

        Per Matter spec, ClearUser also wipes any associated credentials
        and schedules in one operation, so we don't need to call
        ``clear_lock_credential`` separately.
        """
        started_at = time.monotonic()
        try:
            await hass.services.async_call(
                _MATTER_DOMAIN,
                "clear_lock_user",
                {"entity_id": entity_id, "user_index": slot},
                blocking=True,
            )
        except HomeAssistantError as exc:
            matter_status = _extract_matter_status(exc)
            extra: Dict[str, Any] = {}
            if matter_status is not None:
                extra["matter_status"] = matter_status
            result = ProviderResult(
                slot=slot,
                method="matter_clear_user",
                verified=False,
                error=_format_clear_error(matter_status, exc),
                extra=extra,
            )
            _log_clear_outcome(entity_id, slot, started_at, result, exc=exc)
            return result
        except Exception as exc:  # pragma: no cover - belt-and-suspenders
            result = ProviderResult(
                slot=slot,
                method="matter_clear_user",
                verified=False,
                error=f"clear_lock_user: {exc}",
            )
            LOGGER.exception(
                "matter.clear_lock_user raised non-HA exception for %s slot %d",
                entity_id,
                slot,
            )
            _log_clear_outcome(entity_id, slot, started_at, result, exc=exc)
            return result

        result = ProviderResult(slot=slot, method="matter_clear_user", verified=True)
        _log_clear_outcome(entity_id, slot, started_at, result)
        return result

    async def read_codes(
        self,
        hass: HomeAssistant,
        entity_id: str,
        max_slots: int = 30,
    ) -> List[SlotInfo]:
        """Probe each slot via ``matter.get_lock_credential_status``.

        Matter doesn't return PIN values (write-only on the lock), so the
        ``code`` field in the result is always None — only occupancy is
        observable.  This is intentional: callers that need to display
        codes should keep them in their own datastore.
        """
        results: List[SlotInfo] = []
        for slot in range(1, max_slots + 1):
            status = await _get_credential_status(hass, entity_id, slot)
            if status is None:
                continue
            results.append(
                SlotInfo(
                    slot=slot,
                    occupied=bool(status.get("credential_exists")),
                    code=None,
                )
            )
        return results

    async def get_capabilities(
        self,
        hass: HomeAssistant,
        entity_id: str,
    ) -> CapabilityInfo:
        info = await _get_lock_info(hass, entity_id)
        if not info:
            return CapabilityInfo(supports_access_codes=False)

        supports = (
            bool(info.get("supports_user_management"))
            and _PIN in (info.get("supported_credential_types") or [])
        )
        max_slots = info.get("max_pin_users") or info.get("max_users")
        return CapabilityInfo(
            supports_access_codes=supports,
            max_slots=max_slots,
            extra=info,
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _extract_entity_response(
    response: Optional[Dict[str, Any]], entity_id: str
) -> Optional[Dict[str, Any]]:
    """HA's call_service with return_response wraps results by entity_id."""
    if not isinstance(response, dict):
        return None
    if entity_id in response and isinstance(response[entity_id], dict):
        return response[entity_id]
    return response


def _is_duplicate_credential_error(exc: BaseException) -> bool:
    """Detect HA's ``SetCredentialFailedError`` with status=duplicate.

    The matter integration raises ``HomeAssistantError`` subclasses with
    ``translation_placeholders={"status": "<dl_status>"}``.  The
    structured field is the most reliable signal; we also fall back to
    a substring match on the rendered string for safety in case the
    helper class shape changes between HA releases.
    """
    placeholders = getattr(exc, "translation_placeholders", None)
    if isinstance(placeholders, dict) and placeholders.get("status") == _DUPLICATE_STATUS:
        return True

    return _DUPLICATE_STATUS in str(exc).lower()


def _extract_matter_status(exc: BaseException) -> Optional[str]:
    """Pull the structured Matter status out of an HA exception.

    HA's matter lock helpers raise with
    ``translation_placeholders={"status": "<value>"}`` where ``<value>``
    is one of the DlStatus strings (``failure``, ``duplicate``,
    ``occupied``) or ``unknown(<int>)`` for IM-level codes the helper
    didn't map.  We surface this verbatim so operators can correlate
    against Matter spec status tables.
    """
    placeholders = getattr(exc, "translation_placeholders", None)
    if isinstance(placeholders, dict):
        status = placeholders.get("status")
        if isinstance(status, str) and status:
            return status
    return None


def _format_set_error(matter_status: Optional[str], exc: BaseException) -> str:
    """Build the ProviderResult.error string with the Matter status if present.

    Including the status code in the error string means it propagates
    through Orion's ``DeviceService.classify_action_body/1`` /
    ``ActivityService.format_error_reason/1`` chain into the user-facing
    activity log without any further plumbing.
    """
    if matter_status:
        return f"set_lock_credential: matter_status={matter_status}: {exc}"
    return f"set_lock_credential: {exc}"


def _format_clear_error(matter_status: Optional[str], exc: BaseException) -> str:
    """Mirror of ``_format_set_error`` for the clear path."""
    if matter_status:
        return f"clear_lock_user: matter_status={matter_status}: {exc}"
    return f"clear_lock_user: {exc}"


def _log_set_outcome(
    entity_id: str,
    slot: int,
    code: str,
    started_at: float,
    result: ProviderResult,
    *,
    exc: Optional[BaseException] = None,
) -> None:
    """Emit one structured wide-event log line per ``set_code`` attempt.

    PIN bytes are never logged; only the length so length-related
    rejections (``MinPINCodeLength`` / ``MaxPINCodeLength``) can be
    correlated against lock attributes without leaking secrets.
    """
    duration_ms = int((time.monotonic() - started_at) * 1000)
    matter_status = result.extra.get("matter_status") or result.extra.get("status")
    log_fn = LOGGER.info if result.verified else LOGGER.error
    log_fn(
        "matter set_code outcome entity_id=%s slot=%d code_length=%d "
        "method=%s verified=%s matter_status=%s duration_ms=%d error=%s",
        entity_id,
        slot,
        len(code),
        result.method,
        result.verified,
        matter_status or "-",
        duration_ms,
        _format_log_error(result.error, exc),
    )


def _log_clear_outcome(
    entity_id: str,
    slot: int,
    started_at: float,
    result: ProviderResult,
    *,
    exc: Optional[BaseException] = None,
) -> None:
    """Emit one structured wide-event log line per ``clear_code`` attempt."""
    duration_ms = int((time.monotonic() - started_at) * 1000)
    matter_status = result.extra.get("matter_status")
    log_fn = LOGGER.info if result.verified else LOGGER.error
    log_fn(
        "matter clear_code outcome entity_id=%s slot=%d "
        "method=%s verified=%s matter_status=%s duration_ms=%d error=%s",
        entity_id,
        slot,
        result.method,
        result.verified,
        matter_status or "-",
        duration_ms,
        _format_log_error(result.error, exc),
    )


def _format_log_error(error: Optional[str], exc: Optional[BaseException]) -> str:
    if error:
        return error
    if exc is not None:
        return f"{type(exc).__name__}: {exc}"
    return "-"


async def _get_credential_status(
    hass: HomeAssistant, entity_id: str, slot: int
) -> Optional[Dict[str, Any]]:
    try:
        response = await hass.services.async_call(
            _MATTER_DOMAIN,
            "get_lock_credential_status",
            {
                "entity_id": entity_id,
                "credential_type": _PIN,
                "credential_index": slot,
            },
            blocking=True,
            return_response=True,
        )
    except Exception:
        LOGGER.debug(
            "matter.get_lock_credential_status failed for %s slot %d",
            entity_id,
            slot,
            exc_info=True,
        )
        return None

    return _extract_entity_response(response, entity_id)


async def _get_lock_info(
    hass: HomeAssistant, entity_id: str
) -> Optional[Dict[str, Any]]:
    try:
        response = await hass.services.async_call(
            _MATTER_DOMAIN,
            "get_lock_info",
            {"entity_id": entity_id},
            blocking=True,
            return_response=True,
        )
    except Exception:
        LOGGER.debug(
            "matter.get_lock_info failed for %s", entity_id, exc_info=True
        )
        return None
    return _extract_entity_response(response, entity_id)
