"""State change filtering for Staykey gateway forwarding.

Determines which HA state_changed events are meaningful enough to forward
as activity updates. Filters out:
- Attribute-only changes (state string unchanged)
- Transitional cover states (opening/closing)

The comparison value comes from the event being handled, so the decision
holds no state of its own.
"""

from __future__ import annotations

COVER_TERMINAL_STATES = frozenset({"open", "closed"})


def should_forward_state(
    entity_id: str,
    state_value: str,
    previous_state: str | None,
) -> bool:
    """Decide whether a state change should be forwarded to the gateway.

    ``previous_state`` is the state the entity is changing *from*, taken
    from the Home Assistant event itself rather than from anything the
    integration remembers, so the answer is the same after a restart, after
    a config entry reload, and for every config entry on the same hub.

    Returns False for:
    - Repeated reports of the same state (attribute-only HA events)
    - Cover transitional states ("opening", "closing")
    """
    if state_value == previous_state:
        return False

    domain = entity_id.split(".")[0] if "." in entity_id else ""

    return not (domain == "cover" and state_value not in COVER_TERMINAL_STATES)
