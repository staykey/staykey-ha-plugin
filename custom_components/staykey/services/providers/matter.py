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
  If the slot is occupied, HA sends ``SetUser(kModify, ...)`` which
  some Matter locks (Ultraloq Bolt SE confirmed) reject with
  ``InvalidCommand (0x85)``.
* The "auto-allocate empty slot then Add" branch only runs when
  ``user_index=None``, which would force us to give up the
  ``slot == user_index`` invariant.

Per the Matter 1.x spec (DoorLock cluster, SetCredential command), when
``operationType=kAdd`` and ``userIndex`` references a non-existent
user, the lock auto-creates that user with default attributes.  HA's
``set_lock_credential`` helper picks Add vs Modify based on the
**credential** slot's occupancy (no empty-slot guard), so a single
``set_lock_credential`` call with ``user_index=slot`` covers both the
"new user + new credential" and "update existing credential" cases for
arbitrary caller-supplied slot numbers.

## SetCredential parameter conservatism

Real-world testing on the Ultraloq Bolt SE turned up a fresh-Add
rejection with HA-rendered status ``unknown(133)``.  HA only maps
DlStatus values 0x00-0x03 in ``SET_CREDENTIAL_STATUS_MAP``, so anything
else surfaces as ``unknown(<int>)``; ``133 = 0x85`` is the Matter
Interaction Model ``INVALID_COMMAND`` general status code, which means
the lock rejected the command before applying it.  The most likely
irritants the spec marks as caller-optional but some implementations
choke on:

* ``userStatus`` set explicitly on a fresh ``kAdd``.  Spec says the
  lock should default to ``kOccupiedEnabled`` when null; the Bolt SE
  appears to reject the explicit value.  We omit it on the wire and
  let the lock pick its default.
* ``userType`` left null on a fresh ``kAdd``.  Spec says the lock
  should default to ``kUnrestrictedUser``; the Bolt SE appears to
  require it explicitly.  We always send ``unrestricted_user``.

This keeps the happy path identical for spec-compliant locks (Z-Wave
locks bridged via Matter, Aqara, Schlage Sense Pro, etc.) while
unblocking the Bolt SE.

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
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from homeassistant.exceptions import HomeAssistantError

from ..lock_provider import CapabilityInfo, LockProvider, ProviderResult, SlotInfo

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

_MATTER_DOMAIN = "matter"
_PIN = "pin"
_USER_TYPE_DEFAULT = "unrestricted_user"

# Kept for backwards compatibility / external readers; no longer sent
# on set_lock_credential — see module docstring (Bolt SE workaround).
_USER_STATUS_ENABLED = "occupied_enabled"

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
          and attaches the PIN to it.
        * If the slot already holds a credential, HA sends ``kModify``
          to update the PIN bytes.
        * If the new PIN bytes match what's already there, the lock
          returns ``duplicate`` status — we treat that as success.

        We deliberately omit ``user_status`` and always send
        ``user_type=unrestricted_user`` — see this module's docstring
        for the rationale (Bolt SE Add-rejection workaround).

        Falls back to ``matter.get_lock_credential_status`` only if the
        set call returns without a usable ``credential_index`` and
        didn't raise.
        """
        request_payload: Dict[str, Any] = {
            "entity_id": entity_id,
            "credential_type": _PIN,
            "credential_data": str(code),
            "credential_index": slot,
            "user_index": slot,
            "user_type": _USER_TYPE_DEFAULT,
        }
        LOGGER.debug(
            "matter.set_lock_credential request: entity_id=%s slot=%d "
            "user_type=%s (user_status omitted intentionally)",
            entity_id,
            slot,
            _USER_TYPE_DEFAULT,
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
                LOGGER.info(
                    "matter.set_lock_credential reported duplicate for %s slot %d; "
                    "treating as verified no-op",
                    entity_id,
                    slot,
                )
                return ProviderResult(
                    slot=slot,
                    method="matter_set_credential_duplicate",
                    verified=True,
                    extra={"status": _DUPLICATE_STATUS},
                )
            matter_status = _extract_matter_status(exc)
            LOGGER.error(
                "matter.set_lock_credential failed for %s slot %d "
                "(matter_status=%s): %s",
                entity_id,
                slot,
                matter_status or "<unknown>",
                exc,
            )
            extra: Dict[str, Any] = {}
            if matter_status is not None:
                extra["matter_status"] = matter_status
            return ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=False,
                error=_format_set_error(matter_status, exc),
                extra=extra,
            )
        except Exception as exc:
            LOGGER.exception(
                "matter.set_lock_credential failed for %s slot %d", entity_id, slot
            )
            return ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=False,
                error=f"set_lock_credential: {exc}",
            )

        per_entity = _extract_entity_response(set_response, entity_id)
        if per_entity and per_entity.get("credential_index") is not None:
            return ProviderResult(
                slot=slot,
                method="matter_set_credential",
                verified=True,
                extra={
                    "credential_index": per_entity.get("credential_index"),
                    "user_index": per_entity.get("user_index"),
                    "next_credential_index": per_entity.get("next_credential_index"),
                },
            )

        status = await _get_credential_status(hass, entity_id, slot)
        if status and status.get("credential_exists"):
            return ProviderResult(
                slot=slot,
                method="matter_active_read",
                verified=True,
                extra={"user_index": status.get("user_index")},
            )

        return ProviderResult(
            slot=slot,
            method="matter_set_credential",
            verified=False,
            error="set_lock_credential returned no credential_index and active read did not confirm",
        )

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
        try:
            await hass.services.async_call(
                _MATTER_DOMAIN,
                "clear_lock_user",
                {"entity_id": entity_id, "user_index": slot},
                blocking=True,
            )
        except Exception as exc:
            LOGGER.exception(
                "matter.clear_lock_user failed for %s slot %d", entity_id, slot
            )
            return ProviderResult(
                slot=slot,
                method="matter_clear_user",
                verified=False,
                error=f"clear_lock_user: {exc}",
            )

        return ProviderResult(slot=slot, method="matter_clear_user", verified=True)

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
