"""State change filtering for Staykey gateway forwarding.

Determines which HA state_changed events are meaningful enough to forward
as activity updates. Filters out:
- Attribute-only changes (state string unchanged)
- Transitional cover states (opening/closing)
"""

from __future__ import annotations

COVER_TERMINAL_STATES = frozenset({"open", "closed"})


def should_forward_state(
    entity_id: str,
    state_value: str,
    last_sent_state: str | None,
) -> bool:
    """Decide whether a state change should be forwarded to the gateway.

    Returns False for:
    - Repeated reports of the same state (attribute-only HA events)
    - Cover transitional states ("opening", "closing")
    """
    if state_value == last_sent_state:
        return False

    domain = entity_id.split(".")[0] if "." in entity_id else ""

    return not (domain == "cover" and state_value not in COVER_TERMINAL_STATES)
