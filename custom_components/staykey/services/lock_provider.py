"""Protocol-agnostic lock-provider abstraction.

The plugin used to call ``zwave_js.set_lock_usercode`` directly from
``handlers/lock.py``.  As Staykey adds Matter support (HA 2026.4 lock
manager) and potentially other smart-home protocols in the future, the
handler shouldn't know which underlying HA service to invoke.  Instead
it asks a :class:`LockProvider` selected from the device's protocol.

Concrete providers live under ``services/providers/`` and all return the
same :class:`ProviderResult` / :class:`SlotInfo` / :class:`CapabilityInfo`
shapes so Orion's Elixir per-protocol modules can stay in lockstep with
the plugin path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@dataclass
class ProviderResult:
    """Outcome of a set_code / clear_code operation.

    Fields mirror what Orion's per-protocol modules return so the same
    JSON makes it back to the worker regardless of which HA path was used.
    """

    slot: int
    method: str  # e.g. "zwave_set_and_verify", "matter_set_credential"
    verified: bool
    attempts: int = 1
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SlotInfo:
    """One row in a code-slot listing."""

    slot: int
    occupied: bool
    code: Optional[str] = None


@dataclass
class CapabilityInfo:
    """Capability summary for a lock entity.

    ``extra`` is protocol-specific (e.g. Z-Wave node statistics, Matter
    feature map bits).
    """

    supports_access_codes: bool
    max_slots: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LockProvider(Protocol):
    """Per-protocol lock operations.

    All methods take an HA ``entity_id`` (the lock entity) and return the
    typed result objects defined in this module.
    """

    name: str
    """Short identifier, e.g. ``"zwave"`` or ``"matter"``."""

    async def set_code(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
        code: str,
    ) -> ProviderResult: ...

    async def clear_code(
        self,
        hass: HomeAssistant,
        entity_id: str,
        slot: int,
    ) -> ProviderResult: ...

    async def read_codes(
        self,
        hass: HomeAssistant,
        entity_id: str,
        max_slots: int = 30,
    ) -> List[SlotInfo]: ...

    async def get_capabilities(
        self,
        hass: HomeAssistant,
        entity_id: str,
    ) -> CapabilityInfo: ...


class UnsupportedProtocolError(RuntimeError):
    """Raised when no provider can be selected for a given entity."""
