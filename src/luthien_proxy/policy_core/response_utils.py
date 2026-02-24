"""Utilities for working with LiteLLM ModelResponse objects."""

from __future__ import annotations

from typing import Any, cast

from litellm.types.utils import Choices, Message, ModelResponse


def extract_tool_calls_from_response(response: ModelResponse) -> list[dict[str, Any]]:
    """Extract tool calls from a complete ModelResponse.

    Args:
        response: ModelResponse from LLM

    Returns:
        List of tool call dicts with keys: id, type, name, arguments
    """
    if not response.choices:
        return []

    first_choice = cast(Choices, response.choices[0])
    message = cast(Message, first_choice.message)

    if not message.tool_calls:
        return []

    tool_calls = []
    for tc in message.tool_calls:
        tool_call = {
            "id": getattr(tc, "id", ""),
            "type": getattr(tc, "type", "function"),
            "name": getattr(getattr(tc, "function", None), "name", ""),
            "arguments": getattr(getattr(tc, "function", None), "arguments", ""),
        }
        tool_calls.append(tool_call)

    return tool_calls


__all__ = [
    "extract_tool_calls_from_response",
]
