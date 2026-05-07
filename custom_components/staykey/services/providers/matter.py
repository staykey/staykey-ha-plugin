"""Matter LockProvider (Home Assistant 2026.4 Matter Lock Manager).

Uses ``matter.set_lock_credential``, ``matter.clear_lock_user``,
``matter.get_lock_credential_status``, and related services from
Home Assistant 2026.4+.

## Credential index vs user index

The integration maps each caller ``slot`` to Matter ``credential_index``.
The lock may choose ``user_index`` on Add; we read it from HA responses.
Older approaches that forced ``slot == user_index`` fail on several vendor
implementations that only auto-create users when ``user_index`` is omitted
(null on the wire), even though the Matter spec also describes an alternate
path.

Payloads follow the same Add/Modify split as Home Assistant's Matter Lock
Manager UI, which is broadly compatible across Matter locks and bridges.

## Add vs Modify

We preflight with ``get_lock_credential_status`` and send one
``set_lock_credential``:

==================================  ================  ==========  =====================  ===========
Slot state                          operation         user_index  user_type              user_status
==================================  ================  ==========  =====================  ===========
Empty slot                          HA picks kAdd     *null*      ``unrestricted_user``  *null*
Occupied slot                       HA picks kModify  existing N  *null*                 *null*
==================================  ================  ==========  =====================  ===========

We do not stack multiple PIN credentials on one ``user_index``: many locks
cap total PIN credentials at ``max_pin_users``, and the integration does
not yet expose rich enough lock-bus events for multi-credential user rows
to be auditable.

On Add we send ``user_type`` and omit ``user_status`` (null on wire). Some
vendor firmware misbehaves if ``user_status`` is set on Add (SetCredential
may succeed while the keypad rejects every PIN).

If the lock reports ``duplicate``, the PIN is already present; we treat that
as success so automated retries converge.

## Clear path

We look up ``user_index`` when available and prefer ``clear_lock_user`` so
the user and attached credentials are removed atomically per the Matter
spec.

## Concurrency and logging

``set_code`` / ``clear_code`` for the same ``entity_id`` are serialized with
an asyncio lock to prevent read/modify races. Structured logs record
``code_length`` but never the PIN itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from weakref import WeakValueDictionary

from homeassistant.exceptions import HomeAssistantError

from ..lock_provider import CapabilityInfo, LockProvider, ProviderResult, SlotInfo

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

# Per-entity asyncio.Lock to serialize Matter set/clear/read operations
# on a single physical lock. Without this, two operations on the same
# entity (e.g. overlapping set and clear jobs) can race their preflight
# race their preflight reads against the other's writes (TOCTOU): the
# preflight sees the slot as empty, takes the Add path, but by the time
# the SetCredential lands the other op has already added a credential
# at that index. Using a WeakValueDictionary so locks are GC'd when
# entities are removed.
_ENTITY_LOCKS: "WeakValueDictionary[str, asyncio.Lock]" = WeakValueDictionary()


def _get_entity_lock(entity_id: str) -> asyncio.Lock:
    lock = _ENTITY_LOCKS.get(entity_id)
    if lock is None:
        lock = asyncio.Lock()
        _ENTITY_LOCKS[entity_id] = lock
    return lock

_MATTER_DOMAIN = "matter"
_PIN = "pin"

# Matches HA's ``USER_TYPE_MAP`` enum — sent on the wire for the Add
# path so the lock auto-creates a non-restricted (always-valid) user.
#
# We deliberately do **not** send ``user_status`` alongside it: the HA
# Matter Lock Manager UI omits it on Add, and sending ``occupied_enabled``
# here has been observed on some firmware to leave the credential written
# but the keypad rejecting PINs even when SetCredential returns success.
# Omitting ``user_status`` matches that UI behavior.
_USER_TYPE_DEFAULT = "unrestricted_user"

# DoorLock SetCredential status codes that are non-fatal for our use
# case.  Mapped from chip.clusters.DoorLock.Enums.DlStatus by HA in
# homeassistant/components/matter/lock_helpers.py:SET_CREDENTIAL_STATUS_MAP.
_DUPLICATE_STATUS = "duplicate"

# Matter IM-level ``Status::InvalidCommand`` rendered through HA's
# ``unknown(<int>)`` fallback when the status didn't match
# ``SET_CREDENTIAL_STATUS_MAP``.  In practice this is what real-world
# locks return when ``credential_index`` exceeds hardware limits — e.g.
# requesting slot 11 on a lock that only allows 10 PIN credentials.
_UNKNOWN_INVALID_FIELD_STATUS = "unknown(133)"


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
        async with _get_entity_lock(entity_id):
            return await self._set_code_locked(hass, entity_id, slot, code)

    async def _set_code_locked(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
        code: str,
    ) -> ProviderResult:
        """Set a PIN credential for *slot*, auto-creating the user if needed.

        Two-branch single ``matter.set_lock_credential`` call.  We
        preflight the slot via ``matter.get_lock_credential_status`` to
        decide between Add and Modify:

        * **Modify path (slot has a credential)** — send
          ``credential_index=slot``,
          ``user_index=<existing user from GetCredentialStatus>``, both
          ``user_status`` and ``user_type`` null.  HA dispatches
          ``SetCredential(kModify)`` and the lock updates the PIN
          bytes for the existing user.
        * **Add path (slot is empty)** — send
          ``credential_index=slot``, ``user_index=null``,
          ``user_type=unrestricted_user``, ``user_status=null``.  HA
          dispatches ``SetCredential(kAdd)``; the lock auto-allocates a
          fresh user with the supplied user_type and attaches the PIN
          at our ``credential_index``.  Each caller slot gets its own
          auto-allocated user — we don't stack multiple credentials onto a
          single ``user_index``.

        Stacking multiple PINs on one ``user_index`` (per Matter §5.2.4.41)
        often yields no real capacity gain because firmware frequently caps
        total PIN credentials at ``max_pin_users``. Keeping one credential
        per user keeps behavior predictable.

        If the lock returns ``duplicate`` (same PIN bytes already
        programmed), we treat that as a verified no-op so automated retries
        converge.

        See the module docstring for why we never send a non-null
        userIndex pointing at an *empty* user slot on locks that only
        accept Add with a null ``user_index``.
        """
        started_at = time.monotonic()

        try:
            existing = await _get_credential_status(hass, entity_id, slot)
        except PreflightUnavailable as exc:
            # Fail safe: we don't know if the slot is occupied. Adding a
            # PIN to an occupied slot can produce undefined behaviour on
            # some firmware (silent overwrite, error, or worse). Bail
            # Return a definitive failure to the caller (upstream marks
            # this class of error as non-retriable).
            result = ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=False,
                error=f"set_lock_credential: preflight_unavailable: {exc.original or exc}",
                extra={
                    "operation": "preflight",
                    "preflight_error": str(exc.original or exc),
                },
            )
            _log_set_outcome(entity_id, slot, code, "preflight", started_at, result, exc=exc.original)
            return result

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
            # fresh user. ``user_type`` describes the auto-created user.
            # ``user_status`` is intentionally omitted — see
            # ``_USER_TYPE_DEFAULT`` (some locks reject keypad entry if
            # ``user_status`` is set on Add).
            request_payload["user_type"] = _USER_TYPE_DEFAULT
        LOGGER.debug(
            "matter.set_lock_credential request: entity_id=%s slot=%d "
            "operation=%s code_length=%d user_index=%s",
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

            await _enrich_with_capacity_context(
                hass, entity_id, slot, matter_status, extra
            )

            result = ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=False,
                error=_format_set_error(matter_status, exc, extra),
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

        try:
            status = await _get_credential_status(hass, entity_id, slot)
        except PreflightUnavailable:
            # Fall through to "no confirmation" failure path below.
            status = None

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
        async with _get_entity_lock(entity_id):
            return await self._clear_code_locked(hass, entity_id, slot)

    async def _clear_code_locked(
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
          no-op — important when automation retries an already-applied clear.
        * If the slot has a credential but no associated ``user_index``
          (orphaned credential, shouldn't happen but defensively
          handled), we fall back to ``matter.clear_lock_credential``
          which removes just the credential.
        """
        started_at = time.monotonic()

        try:
            existing = await _get_credential_status(hass, entity_id, slot)
        except PreflightUnavailable as exc:
            result = ProviderResult(
                slot=slot,
                method="matter_clear_user",
                verified=False,
                error=f"clear_lock_user: preflight_unavailable: {exc.original or exc}",
                extra={"preflight_error": str(exc.original or exc)},
            )
            _log_clear_outcome(entity_id, slot, started_at, result, exc=exc.original)
            return result

        if existing is None:
            # HA responded but didn't include data for this entity/slot.
            # Treat as "no credential to clear" (verified no-op) so retries
            # of an already-satisfied request still converge.
            result = ProviderResult(
                slot=slot,
                method="matter_clear_already_empty",
                verified=True,
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
            try:
                status = await _get_credential_status(hass, entity_id, slot)
            except PreflightUnavailable:
                # Skip the slot rather than misreporting it as empty.
                # Caller can re-issue the read later.
                continue
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
        return CapabilityInfo(
            supports_access_codes=supports,
            max_slots=_extract_max_slots(info),
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


def _format_set_error(
    matter_status: Optional[str],
    exc: BaseException,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the ProviderResult.error string with the Matter status if present.

    Including the status code gives operators a clearer message in Staykey
    dashboards and logs without extra parsing steps.

    When ``extra`` contains a ``reason`` field (e.g. ``slot_out_of_range``)
    we prepend it to the message — this is how the operator sees
    "the lock only has 10 PIN slots" instead of the cryptic
    ``unknown(133)``.
    """
    reason = extra.get("reason") if extra else None
    max_slots = extra.get("max_slots") if extra else None

    if reason == "slot_out_of_range" and max_slots is not None:
        slot = extra.get("slot") if extra else None
        slot_part = f"slot={slot} " if slot is not None else ""
        return (
            "set_lock_credential: slot_out_of_range "
            f"({slot_part}max_slots={max_slots}, "
            f"matter_status={matter_status or 'unknown'}): {exc}"
        )

    if matter_status:
        return f"set_lock_credential: matter_status={matter_status}: {exc}"
    return f"set_lock_credential: {exc}"


async def _enrich_with_capacity_context(
    hass: HomeAssistant,
    entity_id: str,
    slot: int,
    matter_status: Optional[str],
    extra: Dict[str, Any],
) -> None:
    """Look up the lock's PIN-slot capacity to classify ``unknown(133)``.

    Real-world locks return Matter IM ``Status::InvalidCommand`` (0x85)
    via the ``unknown(133)`` fallback when ``credential_index`` exceeds
    ``NumberOfPINCredentialsSupported``.  HA's
    :pyfunc:`SET_CREDENTIAL_STATUS_MAP` doesn't translate this to a
    DlStatus, so we have no per-status hint to act on.  Instead we
    do a one-shot ``matter.get_lock_info`` lookup and, if the slot is
    out of range, mark the failure as ``slot_out_of_range`` with the
    advertised ``max_slots`` so operators and UIs see *why* the lock
    rejected the call rather than only ``unknown(133)``.

    The lookup is best-effort — if the lock can't be queried (unstable
    connection, integration version mismatch) we leave ``extra``
    untouched and the error falls back to the raw matter_status.
    """
    if matter_status != _UNKNOWN_INVALID_FIELD_STATUS:
        return

    info = await _get_lock_info(hass, entity_id)
    if not info:
        return

    max_slots = _extract_max_slots(info)
    if not isinstance(max_slots, int) or max_slots <= 0:
        return

    extra["max_slots"] = max_slots
    extra["slot"] = slot
    if slot > max_slots:
        extra["reason"] = "slot_out_of_range"
    else:
        # Slot is in range but the lock still rejected with 0x85 —
        # most likely the user table is full (every PIN user occupied)
        # or the lock disagrees with its own advertised capacity.
        extra.setdefault("reason", "lock_rejected")


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
    reason = result.extra.get("reason")
    max_slots = result.extra.get("max_slots")
    log_fn = LOGGER.info if result.verified else LOGGER.error
    log_fn(
        "matter set_code outcome entity_id=%s slot=%d operation=%s "
        "code_length=%d method=%s verified=%s matter_status=%s "
        "user_index=%s reason=%s max_slots=%s duration_ms=%d error=%s",
        entity_id,
        slot,
        operation,
        len(code),
        result.method,
        result.verified,
        matter_status or "-",
        user_index if user_index is not None else "-",
        reason or "-",
        max_slots if max_slots is not None else "-",
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


class PreflightUnavailable(Exception):
    """Raised when a Matter preflight call (status / lock info) fails.

    Distinguishes "we don't know the slot state" from "we know the slot is
    empty". Callers that branch on slot occupancy (e.g.
    ``MatterLockProvider.set_code``'s Add-vs-Modify decision) must fail
    safe rather than treat unknown as empty.
    """

    def __init__(self, message: str, *, original: Optional[BaseException] = None) -> None:
        super().__init__(message)
        self.original = original


async def _get_credential_status(
    hass: HomeAssistant, entity_id: str, slot: int
) -> Optional[Dict[str, Any]]:
    """Look up the credential status for *slot*.

    Returns the per-entity status dict on success. May return ``None``
    when HA responded but didn't include an entity-keyed payload (treat
    as "definitely empty / no data" — distinct from the unknown case).

    Raises :class:`PreflightUnavailable` when the underlying HA service
    call fails, so callers can fail safe rather than guess slot
    occupancy from an error.
    """
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
    except Exception as exc:
        LOGGER.warning(
            "matter.get_lock_credential_status failed for %s slot %d: %s",
            entity_id,
            slot,
            exc,
            exc_info=True,
        )
        raise PreflightUnavailable(
            f"get_lock_credential_status failed for {entity_id} slot {slot}",
            original=exc,
        ) from exc

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
        LOGGER.warning(
            "matter.get_lock_info failed for %s", entity_id, exc_info=True
        )
        return None
    return _extract_entity_response(response, entity_id)


def _extract_max_slots(info: Dict[str, Any]) -> Optional[int]:
    """Pick the PIN slot capacity from a ``get_lock_info`` payload.

    HA's matter integration may surface ``max_pin_users`` (preferred — the
    PIN-credential user count specifically, per
    ``NumberOfPINUsersSupported``) and/or the broader ``max_users``
    (``NumberOfTotalUsersSupported``).  Prefer the PIN-specific number when
    both are present, since users that can't hold PINs aren't useful slots
    for us.

    The Matter spec (§5.2.4.41) implies a lock that also advertises
    ``NumberOfCredentialsSupportedPerUser = C`` can hold ``U × C`` PIN
    credentials. In practice, vendor firmware enforces
    a tighter global cap equal to the user count even when the spec would
    allow more PIN rows per user. We therefore size ``max_slots`` from
    ``max_pin_users`` / ``max_users`` conservatively; hosts with unusual
    hardware can correct capacity in the Staykey device catalog if needed.
    """
    candidates = (
        info.get("max_pin_users"),
        info.get("max_users"),
    )
    for candidate in candidates:
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    return None
