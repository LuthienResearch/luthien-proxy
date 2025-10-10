"""Utilities for working with OpenAI-style streaming chunks."""

from __future__ import annotations

from luthien_proxy.control_plane.conversation.utils import (
    format_function_call_delta,
    format_tool_call_delta,
    message_content_to_text,
    require_dict,
    require_list,
    require_str,
)
from luthien_proxy.types import JSONObject, JSONValue


def extract_delta_text(chunk: JSONObject) -> str:
    """Extract text delta from an OpenAI-style streaming chunk (best-effort)."""
    choices_value = chunk.get("choices")
    if choices_value is None:
        return ""
    choices = require_list(choices_value, "stream chunk choices")
    if not choices:
        return ""
    parts: list[str] = []
    for index, choice_value in enumerate(choices):
        if not isinstance(choice_value, dict):
            continue
        choice = require_dict(choice_value, f"stream chunk choice #{index}")
        delta_value = choice.get("delta")
        if not isinstance(delta_value, dict):
            continue
        delta = require_dict(delta_value, f"stream chunk choice #{index}.delta")
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif content is not None:
            try:
                parts.append(message_content_to_text(content))
            except Exception:
                pass
        function_call_value = delta.get("function_call")
        if isinstance(function_call_value, dict):
            summary = format_function_call_delta(function_call_value)
            if summary:
                parts.append(summary)
        tool_calls_value = delta.get("tool_calls")
        if isinstance(tool_calls_value, list):
            for tool_index, call_value in enumerate(tool_calls_value):
                try:
                    call = require_dict(call_value, f"stream chunk choice #{index}.tool_call#{tool_index}")
                except ValueError:
                    continue
                summary = format_tool_call_delta(call)
                if summary:
                    parts.append(summary)
    return "".join(parts)


def extract_tool_call_details(chunk: JSONObject) -> list[dict[str, JSONValue]]:
    """Return a normalized list of tool call descriptors from a chunk."""
    tool_calls: list[dict[str, JSONValue]] = []
    choices_value = chunk.get("choices")
    if choices_value is None:
        return tool_calls
    choices = require_list(choices_value, "stream chunk choices")
    for index, choice_value in enumerate(choices):
        if not isinstance(choice_value, dict):
            continue
        choice = require_dict(choice_value, f"stream chunk choice #{index}")
        sources = []
        delta_value = choice.get("delta")
        if isinstance(delta_value, dict):
            sources.append(require_dict(delta_value, f"stream chunk choice #{index}.delta"))
        message_value = choice.get("message")
        if isinstance(message_value, dict):
            sources.append(require_dict(message_value, f"stream chunk choice #{index}.message"))
        for source in sources:
            raw_calls = source.get("tool_calls")
            if not isinstance(raw_calls, list):
                continue
            for tool_index, call_value in enumerate(raw_calls):
                if not isinstance(call_value, dict):
                    continue
                call = require_dict(call_value, f"stream chunk choice #{index}.tool_call#{tool_index}")
                normalized: dict[str, JSONValue] = {}
                tool_id = call.get("id")
                if isinstance(tool_id, str):
                    normalized["id"] = tool_id
                call_type = call.get("type")
                if isinstance(call_type, str):
                    normalized["type"] = call_type
                function_payload = call.get("function")
                if isinstance(function_payload, dict):
                    function = require_dict(function_payload, f"tool_call function #{tool_index}")
                    name = function.get("name")
                    if isinstance(name, str):
                        normalized["name"] = name
                    args = function.get("arguments")
                    if isinstance(args, str):
                        normalized["arguments"] = args
                    elif args is not None:
                        normalized["arguments"] = args  # already JSON-like
                else:
                    name = call.get("name")
                    if isinstance(name, str):
                        normalized["name"] = name
                    arguments = call.get("arguments")
                    if isinstance(arguments, str):
                        normalized["arguments"] = arguments
                    elif arguments is not None:
                        normalized["arguments"] = arguments
                if "id" not in normalized:
                    normalized["id"] = require_str(str(tool_index), "tool call id")
                tool_calls.append(normalized)
    return tool_calls
