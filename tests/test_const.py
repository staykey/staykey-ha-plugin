"""Tests for gateway capability constants."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "staykey")
)
from const import GATEWAY_FEATURES  # noqa: E402


def test_raw_device_events_is_advertised():
    assert "raw_device_events" in GATEWAY_FEATURES


def test_existing_features_are_kept():
    for feature in (
        "lock_control",
        "access_code_management",
        "zwave_code_slots",
        "state_streaming",
        "device_discovery",
        "capability_discovery",
        "health_monitoring",
        "diagnostics",
        "batch_operations",
    ):
        assert feature in GATEWAY_FEATURES


def test_no_duplicates():
    assert len(GATEWAY_FEATURES) == len(set(GATEWAY_FEATURES))
