"""ABOUTME: Utilities for parsing conversation requests, responses, and tool calls."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional


def extract_call_id(payload: Mapping[str, Any]) -> Optional[str]:
    """Extract call_id from payload, returning None if not found."""
    candidates = (
        payload.get("litellm_call_id"),
        _metadata_lookup(payload, "litellm_call_id"),
        _params_lookup(payload, "litellm_call_id"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def require_call_id(payload: Mapping[str, Any]) -> str:
    """Extract call_id from payload, raising error if not found."""
    call_id = extract_call_id(payload)
    if not call_id:
        raise ValueError("payload missing litellm_call_id")
    return call_id


def extract_trace_id(payload: Mapping[str, Any]) -> Optional[str]:
    """Extract trace_id from payload, returning None if not found."""
    candidates = (
        payload.get("litellm_trace_id"),
        _metadata_lookup(payload, "litellm_trace_id"),
        _params_lookup(payload, "litellm_trace_id"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def parse_tool_calls(value: Any, *, allow_empty: bool = False) -> list[dict[str, str]]:
    """Parse tool_calls from a message payload."""
    if value is None:
        if allow_empty:
            return []
        raise ValueError("tool_calls value is None")
    if not isinstance(value, list):
        raise TypeError("tool_calls must be a list")
    parsed: list[dict[str, str]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise TypeError("tool call entries must be mappings")
        call_type = raw.get("type")
        if not isinstance(call_type, str) or not call_type:
            call_type = "function"
        call_id = raw.get("id")
        if call_id is not None and not isinstance(call_id, str):
            raise TypeError("tool call id must be a string when present")
        function = raw.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("tool call missing function payload")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("tool function name must be non-empty string")
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            raise TypeError("tool function arguments must be a string")
        parsed.append(
            {
                "id": call_id or f"tool_{index}",
                "type": call_type,
                "name": name,
                "arguments": arguments,
            }
        )
    return parsed


def parse_legacy_function_call(payload: Any) -> dict[str, str]:
    """Parse a legacy function_call payload into tool call format."""
    if not isinstance(payload, Mapping):
        raise TypeError("function_call payload must be a mapping")
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("function_call name must be a non-empty string")
    arguments = payload.get("arguments")
    if not isinstance(arguments, str):
        raise TypeError("function_call arguments must be a string")
    raw_id = payload.get("id")
    identifier = raw_id if isinstance(raw_id, str) and raw_id else "legacy_function_call"
    return {
        "id": identifier,
        "type": "function",
        "name": name,
        "arguments": arguments,
    }


def content_to_text(content: Any) -> str:
    """Convert message content to plain text string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, Mapping):
                raise TypeError("message content items must be mappings")
            item_type = item.get("type")
            if item_type != "text":
                raise ValueError(f"unsupported content part type: {item_type!r}")
            text = item.get("text")
            if not isinstance(text, str):
                raise TypeError("text content part must contain string text")
            parts.append(text)
        return "".join(parts)
    raise TypeError("unsupported message content structure")


def format_messages(messages: Any) -> Iterable[dict[str, Any]]:
    """Format messages list into normalized structure."""
    if messages is None:
        return []
    if not isinstance(messages, list):
        raise TypeError("messages must be a list of dict objects")
    formatted: list[dict[str, Any]] = []
    for index, raw in enumerate(messages):
        if not isinstance(raw, Mapping):
            raise TypeError(f"message #{index} must be a mapping, saw {type(raw)!r}")
        role = raw.get("role")
        if not isinstance(role, str) or not role:
            raise ValueError(f"message #{index} missing role")
        message_type = raw.get("type")
        if message_type is not None and not isinstance(message_type, str):
            raise TypeError(f"message #{index} type must be a string when present")
        content = content_to_text(raw.get("content"))
        formatted.append(
            {
                "index": index,
                "role": role,
                "type": message_type,
                "content": content,
            }
        )
    return formatted


def _metadata_lookup(payload: Mapping[str, Any], key: str) -> Any:
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        return metadata.get(key)
    return None


def _params_lookup(payload: Mapping[str, Any], key: str) -> Any:
    params = payload.get("litellm_params")
    if isinstance(params, Mapping):
        metadata = params.get("metadata")
        if isinstance(metadata, Mapping):
            return metadata.get(key)
    return None


__all__ = [
    "extract_call_id",
    "require_call_id",
    "extract_trace_id",
    "parse_tool_calls",
    "parse_legacy_function_call",
    "content_to_text",
    "format_messages",
]
