"""Tests for state_filter module."""

import sys
from pathlib import Path

import pytest

# Import state_filter directly to avoid pulling in HA-dependent __init__.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "staykey"))
from state_filter import COVER_TERMINAL_STATES, should_forward_state  # noqa: E402


class TestShouldForwardState:
    """Tests for the should_forward_state predicate."""

    # -- Same-state deduplication (all domains) --

    def test_first_state_always_forwarded(self):
        assert should_forward_state("switch.living_room", "on", None) is True

    def test_same_state_skipped(self):
        assert should_forward_state("switch.living_room", "on", "on") is False

    def test_different_state_forwarded(self):
        assert should_forward_state("switch.living_room", "off", "on") is True

    # -- Cover domain filtering --

    def test_cover_open_forwarded(self):
        assert should_forward_state("cover.garage_door", "open", None) is True

    def test_cover_closed_forwarded(self):
        assert should_forward_state("cover.garage_door", "closed", "open") is True

    def test_cover_closing_skipped(self):
        assert should_forward_state("cover.garage_door", "closing", "open") is False

    def test_cover_opening_skipped(self):
        assert should_forward_state("cover.garage_door", "opening", "closed") is False

    def test_cover_same_terminal_state_skipped(self):
        assert should_forward_state("cover.garage_door", "closed", "closed") is False

    def test_cover_unknown_state_skipped(self):
        """Non-terminal, non-standard states on covers are filtered out."""
        assert should_forward_state("cover.garage_door", "unavailable", None) is False

    # -- Climate domain --

    def test_climate_mode_change_forwarded(self):
        assert should_forward_state("climate.entryway", "cool", "off") is True

    def test_climate_same_mode_skipped(self):
        assert should_forward_state("climate.entryway", "cool", "cool") is False

    def test_climate_first_mode_forwarded(self):
        assert should_forward_state("climate.entryway", "heat", None) is True

    def test_climate_off_forwarded(self):
        assert should_forward_state("climate.entryway", "off", "cool") is True

    # -- Switch / light domains --

    def test_switch_on_forwarded(self):
        assert should_forward_state("switch.porch_light", "on", "off") is True

    def test_switch_same_state_skipped(self):
        assert should_forward_state("switch.porch_light", "off", "off") is False

    def test_light_on_forwarded(self):
        assert should_forward_state("light.bedroom", "on", "off") is True

    def test_light_same_state_skipped(self):
        assert should_forward_state("light.bedroom", "on", "on") is False

    # -- Lock domain (passes through; HA plugin skips locks elsewhere) --

    def test_lock_state_forwarded_if_new(self):
        assert should_forward_state("lock.front_door", "locked", None) is True

    def test_lock_same_state_skipped(self):
        assert should_forward_state("lock.front_door", "locked", "locked") is False

    # -- Edge cases --

    def test_entity_without_domain_dot_treated_as_generic(self):
        assert should_forward_state("nodomain", "on", None) is True

    def test_entity_without_domain_same_state_skipped(self):
        assert should_forward_state("nodomain", "on", "on") is False

    def test_empty_entity_id(self):
        assert should_forward_state("", "on", None) is True

    def test_unavailable_generic_device_forwarded(self):
        assert should_forward_state("switch.foo", "unavailable", "on") is True


class TestCoverTerminalStates:
    """Verify the constant contains expected values."""

    def test_contains_open_and_closed(self):
        assert "open" in COVER_TERMINAL_STATES
        assert "closed" in COVER_TERMINAL_STATES

    def test_does_not_contain_transitional(self):
        assert "opening" not in COVER_TERMINAL_STATES
        assert "closing" not in COVER_TERMINAL_STATES
