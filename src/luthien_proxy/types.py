"""Shared type aliases for structured data passed around the proxy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from pydantic import JsonValue

# NOTE: We eagerly constrain JSON-like payloads rather than relying on ``Any``
# so that type checkers surface incorrect assumptions immediately. These
# aliases align with Pydantic's built-in JSON semantics.
JSONValue: TypeAlias = JsonValue
JSONObject: TypeAlias = dict[str, JsonValue]
JSONArray: TypeAlias = list[JsonValue]


@dataclass(frozen=True)
class RawHttpRequest:
    """Captured HTTP request data before any processing.

    This preserves the original client request including headers and body,
    allowing policies to access data that may be lost during format conversion
    (e.g., Anthropic 'system' field, 'metadata' field, custom headers).
    """

    body: JSONObject
    """The raw JSON request body as sent by the client."""

    headers: dict[str, str] = field(default_factory=dict)
    """HTTP headers from the request (lowercase keys)."""

    method: str = "POST"
    """HTTP method (typically POST for LLM API calls)."""

    path: str = ""
    """Request path (e.g., '/v1/messages' or '/v1/chat/completions')."""


__all__ = ["JSONValue", "JSONObject", "JSONArray", "RawHttpRequest"]
