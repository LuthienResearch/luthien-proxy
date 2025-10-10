"""Shared helpers for conversation tracing."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, Literal, Mapping, Optional, Tuple, cast

from luthien_proxy.types import JSONArray, JSONObject, JSONValue

_DEFAULT_JSON_SAFE_MAX_DEPTH = 20


def _is_primitive(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def require_dict(value: object, context: str) -> JSONObject:
    """Ensure *value* is a dict, raising a descriptive error otherwise."""
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a dict; saw {type(value)!r}")
    if not all(isinstance(key, str) for key in value.keys()):
        raise ValueError(f"{context} must use string keys; saw {list(value.keys())!r}")
    return cast(JSONObject, value)


def require_list(value: object, context: str) -> JSONArray:
    """Ensure *value* is a list, raising a descriptive error otherwise."""
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list; saw {type(value)!r}")
    return cast(JSONArray, value)


def require_str(value: object, context: str) -> str:
    """Ensure *value* is a string, raising a descriptive error otherwise."""
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string; saw {type(value)!r}")
    return value


def json_safe(value: object, *, max_depth: int = _DEFAULT_JSON_SAFE_MAX_DEPTH) -> JSONValue:
    """Recursively coerce *value* into a JSON-serializable structure with depth limits."""

    def _inner(current: object, depth: int, seen: set[int]) -> JSONValue:
        if depth > max_depth:
            return "<max-depth-exceeded>"

        if _is_primitive(current):
            return cast(JSONValue, current)

        if isinstance(current, dict):
            current_id = id(current)
            if current_id in seen:
                return "<recursion>"
            seen.add(current_id)
            try:
                return {str(key): _inner(val, depth + 1, seen) for key, val in current.items()}
            finally:
                seen.discard(current_id)

        if isinstance(current, (list, tuple, set)):
            current_id = id(current)
            if current_id in seen:
                return "<recursion>"
            seen.add(current_id)
            try:
                return [_inner(item, depth + 1, seen) for item in current]
            finally:
                seen.discard(current_id)

        if hasattr(current, "__dict__"):
            current_id = id(current)
            if current_id in seen:
                return "<recursion>"
            seen.add(current_id)
            try:
                return {str(key): _inner(val, depth + 1, seen) for key, val in vars(current).items()}
            finally:
                seen.discard(current_id)

        try:
            json.dumps(current)
            return cast(JSONValue, current)
        except Exception:
            try:
                return repr(current)
            except Exception:
                return "<unserializable>"

    return cast(JSONValue, _inner(value, 0, set()))


def _stringify_arguments(value: object) -> str:
    """Convert tool/function call arguments into a readable string."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return repr(value)


def _format_call(label: str, name_value: object, arguments_value: object) -> str:
    """Format a tool/function call with optional name and arguments."""
    prefix = ""
    if isinstance(name_value, str) and name_value:
        prefix = f"[{label} {name_value}] "
    elif label:
        prefix = f"[{label}] "
    args_text = _stringify_arguments(arguments_value)
    if args_text:
        return f"{prefix}{args_text}" if prefix else args_text
    return prefix.strip()


def format_function_call_delta(function_payload: Mapping[str, object]) -> str:
    """Render an assistant function_call payload as human-readable text."""
    function = require_dict(function_payload, "function call payload")
    return _format_call("function", function.get("name"), function.get("arguments"))


def format_tool_call_delta(call_payload: Mapping[str, object]) -> str:
    """Render an assistant tool_call payload as human-readable text."""
    call = require_dict(call_payload, "tool call payload")
    func_payload = call.get("function")
    if isinstance(func_payload, dict):
        func = require_dict(func_payload, "tool call function")
        return _format_call("tool", func.get("name"), func.get("arguments"))
    return _format_call("tool", call.get("name"), call.get("arguments"))


def format_tool_calls_summary(tool_calls: Iterable[object]) -> str:
    """Condense a list of tool calls into newline-separated text."""
    lines: list[str] = []
    for index, call_value in enumerate(tool_calls):
        try:
            call = require_dict(call_value, f"tool call #{index}")
        except ValueError:
            continue
        summary = format_tool_call_delta(call)
        if not summary:
            continue
        call_id = call.get("id")
        if isinstance(call_id, str) and call_id:
            summary = f"{summary} (id: {call_id})"
        lines.append(summary)
    return "\n".join(lines)


def message_content_to_text(content: JSONValue | None) -> str:
    """Flatten OpenAI-style message content into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for index, item in enumerate(content):
            part = require_dict(item, f"message content part #{index}")
            text = part.get("text")
            parts.append(require_str(text, f"message content part #{index}.text"))
        return "".join(parts)
    if isinstance(content, dict):
        if "text" in content:
            return require_str(content.get("text"), "message content text")
        inner = content.get("content")
        if inner is not None:
            return message_content_to_text(inner)
    raise ValueError(f"Unexpected message content type: {type(content)!r}")


def messages_from_payload(payload: object) -> list[Tuple[str, str]]:
    """Extract (role, content) tuples from a request payload."""
    payload_dict = require_dict(payload, "messages payload")
    container_key = "data" if "data" in payload_dict else "request_data"
    if container_key not in payload_dict:
        raise ValueError("messages payload missing 'data' or 'request_data'")
    request_dict = require_dict(payload_dict[container_key], f"payload.{container_key}")
    messages = require_list(request_dict.get("messages"), "payload messages")
    out: list[tuple[str, str]] = []
    for index, msg in enumerate(messages):
        msg_dict = require_dict(msg, f"message entry #{index}")
        role = require_str(msg_dict.get("role"), "message role")
        content = msg_dict.get("content")
        out.append((role, message_content_to_text(cast(JSONValue | None, content))))
    return out


def format_messages(message_pairs: Iterable[Tuple[str, str]]) -> list[dict[str, str]]:
    """Convert (role, content) tuples into list-of-dict form."""
    return [{"role": role, "content": content} for role, content in message_pairs]


def extract_choice_index(chunk: object) -> int:
    """Return the index of the first choice in a streaming chunk."""
    chunk_dict = require_dict(chunk, "stream chunk")
    choices = require_list(chunk_dict.get("choices"), "stream chunk choices")
    if not choices:
        raise ValueError("stream chunk choices list is empty")
    choice = require_dict(choices[0], "stream chunk choice")
    idx = choice.get("index")
    if not isinstance(idx, int):
        raise ValueError("stream chunk choice missing integer index")
    return idx


def delta_from_chunk(chunk: JSONValue | str | None) -> str:
    """Pull the textual delta from a streaming chunk payload."""
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk
    chunk_dict = require_dict(chunk, "stream chunk payload")
    from luthien_proxy.control_plane.utils.streaming import extract_delta_text

    return extract_delta_text(chunk_dict)


def extract_stream_chunk(payload: object) -> JSONValue | None:
    """Peel off envelope wrappers to access the chunk payload."""
    if payload is None:
        return None
    payload_dict = require_dict(payload, "stream chunk envelope")
    for key in ("response", "chunk", "response_obj", "raw_response"):
        if key in payload_dict:
            return payload_dict.get(key)
    return payload_dict


def extract_trace_id(payload: object) -> Optional[str]:
    """Find a trace identifier within a request payload if present."""
    if not isinstance(payload, dict):
        return None
    # Check at the root level first
    trace_id = payload.get("litellm_trace_id")
    if isinstance(trace_id, str) and trace_id:
        return trace_id
    # Then check in request_data
    request_data = payload.get("request_data")
    if isinstance(request_data, dict):
        trace_id = request_data.get("litellm_trace_id")
        if isinstance(trace_id, str) and trace_id:
            return trace_id
    # Finally check in data
    data = payload.get("data")
    if isinstance(data, dict):
        trace_id = data.get("litellm_trace_id")
        if isinstance(trace_id, str) and trace_id:
            return trace_id
    return None


def unwrap_response(payload: object) -> JSONValue | None:
    """Return the response object nested within a hook payload."""
    if payload is None:
        return None
    payload_dict = require_dict(payload, "response envelope")
    for key in ("response", "response_obj", "raw_response"):
        if key in payload_dict:
            return payload_dict[key]
    return payload_dict


def extract_response_text(response: object) -> str:
    """Convert an LLM response payload into plain text."""
    if response is None:
        return ""
    # Don't accept plain strings - expect properly structured response
    response_dict = require_dict(response, "response payload")
    if "choices" in response_dict:
        choices_raw = response_dict["choices"]
        if choices_raw is None:
            return ""
        choices = require_list(choices_raw, "response choices")
        if not choices:
            return ""
        choice = require_dict(choices[0], "response choice")
        if "message" in choice:
            message = require_dict(choice["message"], "response choice.message")
            content_value = message.get("content")
            if content_value is not None:
                try:
                    return message_content_to_text(cast(JSONValue | None, content_value))
                except Exception:
                    pass
            tool_calls_value = message.get("tool_calls")
            if isinstance(tool_calls_value, list):
                summary = format_tool_calls_summary(tool_calls_value)
                if summary:
                    return summary
            function_call_value = message.get("function_call")
            if isinstance(function_call_value, dict):
                return format_function_call_delta(function_call_value)
        if "delta" in choice:
            delta = require_dict(choice["delta"], "response choice.delta")
            content_value = delta.get("content")
            if isinstance(content_value, str):
                return content_value
            if content_value is not None:
                try:
                    return message_content_to_text(cast(JSONValue | None, content_value))
                except Exception:
                    pass
            tool_calls_value = delta.get("tool_calls")
            if isinstance(tool_calls_value, list):
                summary = format_tool_calls_summary(tool_calls_value)
                if summary:
                    return summary
            function_call_value = delta.get("function_call")
            if isinstance(function_call_value, dict):
                return format_function_call_delta(function_call_value)
            return ""
    if "content" in response_dict:
        content = response_dict["content"]
        if isinstance(content, str):
            return content
        # Handle Anthropic Messages API format with content array
        try:
            return message_content_to_text(content)
        except Exception:
            pass
    raise ValueError("Unrecognized response payload structure")


def extract_post_time_ns_from_any(value: object) -> Optional[int]:
    """Search arbitrarily nested data for a `post_time_ns` integer."""
    if isinstance(value, dict):
        candidate = value.get("post_time_ns")
        if isinstance(candidate, (int, float)):
            return int(candidate)
        for key in ("payload", "data", "request_data", "response", "response_obj", "raw_response", "chunk"):
            if key in value:
                nested = extract_post_time_ns_from_any(value.get(key))
                if nested is not None:
                    return nested
        for nested_value in value.values():
            if isinstance(nested_value, (dict, list)):
                nested = extract_post_time_ns_from_any(nested_value)
                if nested is not None:
                    return nested
    elif isinstance(value, list):
        for item in value:
            nested = extract_post_time_ns_from_any(item)
            if nested is not None:
                return nested
    return None


def derive_sequence_ns(fallback_ns: int, *candidates: object) -> int:
    """Pick the first available `post_time_ns`, falling back to *fallback_ns*."""
    for candidate in candidates:
        ns = extract_post_time_ns_from_any(candidate)
        if ns is not None:
            return ns
    return fallback_ns


def strip_post_time_ns(value: JSONValue) -> JSONValue:
    """Remove `post_time_ns` keys from nested structures."""
    if isinstance(value, dict):
        return {key: strip_post_time_ns(inner) for key, inner in value.items() if key != "post_time_ns"}
    if isinstance(value, list):
        return [strip_post_time_ns(cast(JSONValue, item)) for item in value]
    return value


def message_equals(a: Optional[Mapping[str, object]], b: Optional[Mapping[str, object]]) -> bool:
    """Return True when two role/content mappings match after normalization."""
    if a is None or b is None:
        return False
    role_a = str(a.get("role") or "").strip().lower()
    role_b = str(b.get("role") or "").strip().lower()
    content_a = str(a.get("content") or "")
    content_b = str(b.get("content") or "")
    return role_a == role_b and content_a == content_b


def clone_messages(messages: Iterable[object]) -> list[dict[str, str]]:
    """Create shallow copies of role/content message dictionaries."""
    cloned: list[dict[str, str]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "unknown")
        content = str(msg.get("content") or "")
        cloned.append({"role": role, "content": content})
    return cloned


def normalize_status(
    status: str, *, chunk_count: int, completed_at: Optional[datetime]
) -> Literal[
    "pending",
    "success",
    "stream_summary",
    "failure",
    "streaming",
]:
    """Map raw status strings to the canonical status literal."""
    if status in {"success", "stream_summary", "failure", "streaming"}:
        return status  # type: ignore[return-value]
    if completed_at is not None:
        return "success"
    if chunk_count > 0:
        return "streaming"
    return "pending"


__all__ = [
    "require_dict",
    "require_list",
    "require_str",
    "json_safe",
    "message_content_to_text",
    "messages_from_payload",
    "format_messages",
    "format_function_call_delta",
    "format_tool_call_delta",
    "format_tool_calls_summary",
    "extract_choice_index",
    "delta_from_chunk",
    "extract_stream_chunk",
    "extract_trace_id",
    "unwrap_response",
    "extract_response_text",
    "extract_post_time_ns_from_any",
    "derive_sequence_ns",
    "strip_post_time_ns",
    "message_equals",
    "clone_messages",
    "normalize_status",
]
