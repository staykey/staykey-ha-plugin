"""Tests for the wire encoder in ``gateway.protocol``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "staykey")
)
from gateway.protocol import encode  # noqa: E402


def test_unencodable_values_degrade_to_a_string():
    encoded = encode({"x": object()})

    assert isinstance(encoded, str)
    assert '"x"' in encoded
