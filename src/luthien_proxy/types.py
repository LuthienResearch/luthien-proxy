"""Shared type aliases for structured data passed around the proxy."""

from __future__ import annotations

from typing import TypeAlias

from pydantic import JsonValue


# NOTE: We eagerly constrain JSON-like payloads rather than relying on ``Any``
# so that type checkers surface incorrect assumptions immediately. These
# aliases align with Pydantic's built-in JSON semantics.
JSONValue: TypeAlias = JsonValue
JSONObject: TypeAlias = dict[str, JsonValue]
JSONArray: TypeAlias = list[JsonValue]


__all__ = ["JSONValue", "JSONObject", "JSONArray"]
