"""Prepare values for PostgreSQL JSONB storage."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Protocol, cast

_NULL_CHARACTER = "\x00"
_REPLACEMENT_CHARACTER = "�"
_SANITIZATION_MARKER = "_sanitized"


class _ModelDumpable(Protocol):
    """Minimal protocol for Pydantic-compatible JSON serialization."""

    def model_dump(self) -> object: ...


def sanitize_for_jsonb(value: object) -> object:
    r"""Make a value JSON-safe, replace NUL characters, and mark replacements.

    PostgreSQL rejects JSONB strings containing ``\u0000``. The replacement
    character keeps the stored payload readable while making the loss of
    fidelity explicit to consumers of dict payloads.
    """
    sanitized, nul_replaced = _sanitize_json_value(value)
    if nul_replaced == 0 or not isinstance(sanitized, dict):
        return sanitized

    marker = _SANITIZATION_MARKER
    while marker in sanitized:
        marker = f"_{marker}"
    sanitized[marker] = {"nul_replaced": nul_replaced}
    return sanitized


def _sanitize_json_value(value: object) -> tuple[object, int]:
    """Return a recursively JSON-safe value and its number of NUL replacements."""
    if value is None or isinstance(value, (bool, int, float)):
        return value, 0

    if isinstance(value, str):
        count = value.count(_NULL_CHARACTER)
        return value.replace(_NULL_CHARACTER, _REPLACEMENT_CHARACTER), count

    if isinstance(value, datetime):
        return value.isoformat(), 0

    if isinstance(value, bytes):
        return f"b64:{base64.b64encode(value).decode('ascii')}", 0

    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        nul_replaced = 0
        for key, item in value.items():
            key_text = str(key)
            key_count = key_text.count(_NULL_CHARACTER)
            sanitized_key = key_text.replace(_NULL_CHARACTER, _REPLACEMENT_CHARACTER)
            sanitized_item, item_count = _sanitize_json_value(item)
            sanitized[sanitized_key] = sanitized_item
            nul_replaced += key_count + item_count
        return sanitized, nul_replaced

    if isinstance(value, (list, tuple)):
        sanitized_items: list[object] = []
        nul_replaced = 0
        for item in value:
            sanitized_item, item_count = _sanitize_json_value(item)
            sanitized_items.append(sanitized_item)
            nul_replaced += item_count
        return sanitized_items, nul_replaced

    if isinstance(value, set):
        return _sanitize_json_value(sorted(value, key=str))

    if hasattr(value, "model_dump"):
        model = cast(_ModelDumpable, value)
        return _sanitize_json_value(model.model_dump())

    if hasattr(value, "__dict__"):
        return _sanitize_json_value(value.__dict__)

    return _sanitize_json_value(str(value))


__all__ = ["sanitize_for_jsonb"]
