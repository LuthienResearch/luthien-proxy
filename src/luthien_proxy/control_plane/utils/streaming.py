"""Utilities for working with OpenAI-style streaming chunks."""

from __future__ import annotations

from luthien_proxy.control_plane.conversation.utils import (
    format_function_call_delta,
    format_tool_call_delta,
    message_content_to_text,
    require_dict,
    require_list,
)
from luthien_proxy.types import JSONObject


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
