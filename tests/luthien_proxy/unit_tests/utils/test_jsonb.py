"""Tests for PostgreSQL JSONB payload sanitization."""

from __future__ import annotations

import importlib


def test_sanitize_for_jsonb_replaces_nested_nuls_and_marks_payload() -> None:
    """NUL bytes must become replacement characters without changing other JSON values."""
    jsonb = importlib.import_module("luthien_proxy.utils.jsonb")

    payload = {
        "nul\x00key": ["first\x00", {"nested\x00key": "second\x00"}],
        "integer": 7,
        "boolean": False,
        "none": None,
    }

    result = jsonb.sanitize_for_jsonb(payload)

    assert result == {
        "nul�key": ["first�", {"nested�key": "second�"}],
        "integer": 7,
        "boolean": False,
        "none": None,
        "_sanitized": {"nul_replaced": 4},
    }
