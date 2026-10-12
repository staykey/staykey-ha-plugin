"""Tests for the wire encoder in ``gateway.protocol``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "staykey")
)
from gateway.protocol import capabilities_message, encode  # noqa: E402

HA_INSTANCE_ID = "5c9f3a1e2b7d4e6f8a0b1c2d3e4f5a6b"


def test_unencodable_values_degrade_to_a_string():
    encoded = encode({"x": object()})

    assert isinstance(encoded, str)
    assert '"x"' in encoded


def test_capabilities_name_the_home_assistant_installation():
    message = json.loads(
        capabilities_message(
            agent_version="9.9.9", ha_version="2026.10.0", instance_id=HA_INSTANCE_ID
        )
    )

    assert message["type"] == "capabilities"
    assert message["agent_version"] == "9.9.9"
    assert message["instance_id"] == HA_INSTANCE_ID


def test_capabilities_without_an_installation_id_leave_the_key_out():
    message = json.loads(
        capabilities_message(agent_version="9.9.9", ha_version="2026.10.0")
    )

    assert "instance_id" not in message
