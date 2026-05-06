"""Matter LockProvider (HA 2026.4 Matter Lock Manager).

Built on the new ``matter.set_lock_credential`` /
``matter.clear_lock_user`` / ``matter.get_lock_credential_status``
service actions added in Home Assistant 2026.4.

The Staykey ``slot`` is used as ``credential_index`` only.  Earlier
versions of this provider tried to use ``slot`` as ``user_index`` too,
which simplified bookkeeping but assumed every Matter lock implements
the spec's "auto-create-user-at-the-given-userIndex" path.  Real-world
testing showed that the **Ultraloq Bolt SE** (and likely other
vendor-SDK locks) only auto-creates a user when ``userIndex`` is
**null**, not when ``userIndex`` is non-null and refers to an empty
user slot — even though the Matter spec describes both paths.

We therefore mirror the flow the official HA Matter Lock Manager UI
(`frontend#28672 <https://github.com/home-assistant/frontend/pull/28672>`_)
uses, which is known to work on Aqara U200/U300, Yale Assure 2,
Schlage Sense Pro, Z-Wave-via-Matter bridges, and the Bolt SE.

## Two-path SetCredential by slot occupancy

We always pre-flight the slot with ``matter.get_lock_credential_status``
to learn (a) whether HA will pick ``kAdd`` or ``kModify`` and (b) the
current user_index for Modify.  Then we send a single
``matter.set_lock_credential`` shaped for that path:

================  ===============  ==========  ==========  =========================
Slot state        operation        user_index  user_type   user_status
================  ===============  ==========  ==========  =========================
Empty (Add)       HA picks kAdd    *null*      ``unrestricted_user``  *null*
Occupied (Modify) HA picks kModify existing N  *null*      *null*
================  ===============  ==========  ==========  =========================

Both shapes are spec-compliant per Matter 1.x §5.2.4.40 and the
connectedhomeip validity check (``DoorLockServer::SetCredential``,
chip ``16657402aa``):

* **Add + userIndex null** → ``userStatus`` and ``userType`` describe
  the user the lock will auto-create.  Sending ``user_type =
  unrestricted_user`` here is exactly what the HA Matter Lock Manager
  frontend does (and what works on the Bolt SE).
* **Modify + userIndex non-null** → ``userStatus`` and ``userType``
  MUST both be null; the lock keeps the existing user attributes and
  only swaps the PIN bytes.

The combination "Add + userIndex non-null" (which earlier revisions
of this provider used) is *spec-legal* but only viable on locks that
implement the auto-create-on-known-index branch.  The Bolt SE doesn't,
and rejects with ``DlStatus::kInvalidField`` → IM
``Status::InvalidCommand`` (``0x85`` = ``133``); HA renders that as
``unknown(133)`` because ``SET_CREDENTIAL_STATUS_MAP`` only knows the
four lock-level DlStatus values.  This module's prior commit history
(``b46fbe6``, the spec-compliant rollback that followed) walks through
both dead-end shapes for posterity.

## Why we don't call ``matter.set_lock_user`` proactively

We considered a two-step "create user, then attach credential" flow
(``set_lock_user`` first, then ``set_lock_credential``) but the
SetCredential auto-create path is simpler, atomic on the lock side,
and matches the HA UI byte-for-byte.  ``set_lock_user`` is still used
indirectly via ``clear_lock_user`` on the clear path because that's
the one Matter command that wipes a user and all their credentials in
one shot.

## Slot ↔ user_index mapping (relinquished)

Earlier docstrings claimed ``slot == user_index`` to avoid a
mapping-table headache.  That invariant is **no longer maintained**:
the lock allocates ``user_index`` on Add, we read it back from the
SetCredential response, and we surface it in ``ProviderResult.extra``
for callers that want to remember it.  Clearing a slot doesn't need
the cached value — ``clear_code`` just re-queries
``get_lock_credential_status(slot)`` to find the user.

## Verification semantics

* ``matter.set_lock_credential`` returns its result synchronously
  (``credential_index``, ``user_index``, ``next_credential_index``) when
  called with ``return_response=True``.  A successful response means
  the Matter server programmed the credential on the lock.
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
``slot``, ``operation`` (``add`` or ``modify``), ``method``,
``verified``, ``matter_status``, ``user_index`` (if known),
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

# Matches HA's ``USER_TYPE_MAP`` enum — sent on the wire for the Add
# path so the lock auto-creates a non-restricted (always-valid) user.
_USER_TYPE_DEFAULT = "unrestricted_user"

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

        Two-path single ``matter.set_lock_credential`` call, branched on
        the slot's current occupancy as observed via
        ``matter.get_lock_credential_status``:

        * **Empty slot (Add path)** — send ``credential_index=slot``,
          ``user_index=null``, ``user_type=unrestricted_user``,
          ``user_status=null``.  HA's helper sees the empty slot and
          dispatches ``SetCredential(kAdd)``; the lock allocates a
          fresh user with the supplied user_type and attaches the PIN
          at our ``credential_index``.
        * **Occupied slot (Modify path)** — send
          ``credential_index=slot``,
          ``user_index=<existing user from GetCredentialStatus>``, both
          ``user_status`` and ``user_type`` null.  HA dispatches
          ``SetCredential(kModify)`` and the lock updates the PIN
          bytes for the existing user.

        If the lock returns ``duplicate`` (same PIN bytes already
        programmed), we treat that as a verified no-op so Oban retries
        of an op that succeeded silently the first time still
        converge.

        See the module docstring for why this two-path shape is
        necessary (Bolt SE rejects ``kAdd`` with non-null userIndex
        despite the spec).
        """
        started_at = time.monotonic()

        existing = await _get_credential_status(hass, entity_id, slot)
        is_modify = bool(existing and existing.get("credential_exists"))
        operation = "modify" if is_modify else "add"

        request_payload: Dict[str, Any] = {
            "entity_id": entity_id,
            "credential_type": _PIN,
            "credential_data": str(code),
            "credential_index": slot,
        }
        if is_modify:
            existing_user_index = existing.get("user_index") if existing else None
            if existing_user_index is not None:
                request_payload["user_index"] = existing_user_index
            # No user_type / user_status: must both be null on Modify
            # when userIndex is non-null (chip SDK validity check).
        else:
            # No user_index → null on the wire → lock auto-allocates a
            # fresh user.  user_type describes that new user.
            request_payload["user_type"] = _USER_TYPE_DEFAULT
        LOGGER.debug(
            "matter.set_lock_credential request: entity_id=%s slot=%d "
            "operation=%s code_length=%d existing_user_index=%s",
            entity_id,
            slot,
            operation,
            len(code),
            request_payload.get("user_index", "<auto>"),
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
                    extra={
                        "status": _DUPLICATE_STATUS,
                        "operation": operation,
                    },
                )
                _log_set_outcome(entity_id, slot, code, operation, started_at, result)
                return result
            matter_status = _extract_matter_status(exc)
            extra: Dict[str, Any] = {"operation": operation}
            if matter_status is not None:
                extra["matter_status"] = matter_status
            result = ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=False,
                error=_format_set_error(matter_status, exc),
                extra=extra,
            )
            _log_set_outcome(
                entity_id, slot, code, operation, started_at, result, exc=exc
            )
            return result
        except Exception as exc:  # pragma: no cover - belt-and-suspenders
            result = ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=False,
                error=f"set_lock_credential: {exc}",
                extra={"operation": operation},
            )
            LOGGER.exception(
                "matter.set_lock_credential raised non-HA exception for %s slot %d",
                entity_id,
                slot,
            )
            _log_set_outcome(
                entity_id, slot, code, operation, started_at, result, exc=exc
            )
            return result

        per_entity = _extract_entity_response(set_response, entity_id)
        if per_entity and per_entity.get("credential_index") is not None:
            result = ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=True,
                extra={
                    "operation": operation,
                    "credential_index": per_entity.get("credential_index"),
                    "user_index": per_entity.get("user_index"),
                    "next_credential_index": per_entity.get("next_credential_index"),
                },
            )
            _log_set_outcome(entity_id, slot, code, operation, started_at, result)
            return result

        status = await _get_credential_status(hass, entity_id, slot)
        if status and status.get("credential_exists"):
            result = ProviderResult(
                slot=slot,
                method="matter_active_read",
                verified=True,
                extra={
                    "operation": operation,
                    "user_index": status.get("user_index"),
                },
            )
            _log_set_outcome(entity_id, slot, code, operation, started_at, result)
            return result

        result = ProviderResult(
            slot=slot,
            method="matter_set_credential",
            verified=False,
            error="set_lock_credential returned no credential_index and active read did not confirm",
            extra={"operation": operation},
        )
        _log_set_outcome(entity_id, slot, code, operation, started_at, result)
        return result

    async def clear_code(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
    ) -> ProviderResult:
        """Clear the credential at *slot*.

        Looks up the associated ``user_index`` via
        ``matter.get_lock_credential_status`` and then issues
        ``matter.clear_lock_user`` — per Matter spec, ClearUser wipes
        the user and all of their credentials and schedules atomically,
        so we don't need a separate ``clear_lock_credential`` call.

        Edge cases:

        * If the credential slot is already empty we return a verified
          no-op — important for Oban retries.
        * If the slot has a credential but no associated ``user_index``
          (orphaned credential, shouldn't happen but defensively
          handled), we fall back to ``matter.clear_lock_credential``
          which removes just the credential.
        """
        started_at = time.monotonic()

        existing = await _get_credential_status(hass, entity_id, slot)
        if existing is None:
            result = ProviderResult(
                slot=slot,
                method="matter_clear_user",
                verified=False,
                error="clear_lock_user: get_lock_credential_status returned no data",
            )
            _log_clear_outcome(entity_id, slot, started_at, result)
            return result

        if not existing.get("credential_exists"):
            result = ProviderResult(
                slot=slot,
                method="matter_clear_already_empty",
                verified=True,
            )
            _log_clear_outcome(entity_id, slot, started_at, result)
            return result

        user_index = existing.get("user_index")
        if user_index is None:
            return await self._clear_orphan_credential(
                hass, entity_id, slot, started_at
            )

        try:
            await hass.services.async_call(
                _MATTER_DOMAIN,
                "clear_lock_user",
                {"entity_id": entity_id, "user_index": user_index},
                blocking=True,
            )
        except HomeAssistantError as exc:
            matter_status = _extract_matter_status(exc)
            extra: Dict[str, Any] = {"user_index": user_index}
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
                extra={"user_index": user_index},
            )
            LOGGER.exception(
                "matter.clear_lock_user raised non-HA exception for %s slot %d",
                entity_id,
                slot,
            )
            _log_clear_outcome(entity_id, slot, started_at, result, exc=exc)
            return result

        result = ProviderResult(
            slot=slot,
            method="matter_clear_user",
            verified=True,
            extra={"user_index": user_index},
        )
        _log_clear_outcome(entity_id, slot, started_at, result)
        return result

    async def _clear_orphan_credential(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
        started_at: float,
    ) -> ProviderResult:
        """Defensive fallback when a credential exists but has no user.

        Shouldn't happen via our own writes (every ``set_code`` Add
        path leaves the lock with a fresh user) but guard against
        out-of-band manipulation.
        """
        try:
            await hass.services.async_call(
                _MATTER_DOMAIN,
                "clear_lock_credential",
                {
                    "entity_id": entity_id,
                    "credential_type": _PIN,
                    "credential_index": slot,
                },
                blocking=True,
            )
        except HomeAssistantError as exc:
            matter_status = _extract_matter_status(exc)
            extra: Dict[str, Any] = {"orphan": True}
            if matter_status is not None:
                extra["matter_status"] = matter_status
            result = ProviderResult(
                slot=slot,
                method="matter_clear_credential",
                verified=False,
                error=_format_clear_error(matter_status, exc),
                extra=extra,
            )
            _log_clear_outcome(entity_id, slot, started_at, result, exc=exc)
            return result

        result = ProviderResult(
            slot=slot,
            method="matter_clear_credential",
            verified=True,
            extra={"orphan": True},
        )
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
    operation: str,
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
    user_index = result.extra.get("user_index")
    log_fn = LOGGER.info if result.verified else LOGGER.error
    log_fn(
        "matter set_code outcome entity_id=%s slot=%d operation=%s "
        "code_length=%d method=%s verified=%s matter_status=%s "
        "user_index=%s duration_ms=%d error=%s",
        entity_id,
        slot,
        operation,
        len(code),
        result.method,
        result.verified,
        matter_status or "-",
        user_index if user_index is not None else "-",
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
    user_index = result.extra.get("user_index")
    log_fn = LOGGER.info if result.verified else LOGGER.error
    log_fn(
        "matter clear_code outcome entity_id=%s slot=%d "
        "method=%s verified=%s matter_status=%s user_index=%s "
        "duration_ms=%d error=%s",
        entity_id,
        slot,
        result.method,
        result.verified,
        matter_status or "-",
        user_index if user_index is not None else "-",
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
