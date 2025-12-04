"""Utilities for working with LiteLLM ModelResponse objects.

This module provides helper functions for extracting and inspecting data
from ModelResponse objects, particularly for tool calls and streaming chunks.
"""

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
    if not hasattr(response, "choices") or not response.choices:
        return []

    first_choice = response.choices[0]
    first_choice = cast(Choices, first_choice)
    message = first_choice.message if hasattr(first_choice, "message") else {}
    message = cast(Message, message)

    if not hasattr(message, "tool_calls") or not message.tool_calls:
        return []

    tool_calls = []
    for tc in message.tool_calls:
        # Handle both dict and object representations
        if isinstance(tc, dict):
            tool_calls.append(tc)
        else:
            # Convert object to dict
            tool_call = {
                "id": getattr(tc, "id", ""),
                "type": getattr(tc, "type", "function"),
                "name": getattr(getattr(tc, "function", None), "name", ""),
                "arguments": getattr(getattr(tc, "function", None), "arguments", ""),
            }
            tool_calls.append(tool_call)

    return tool_calls


def chunk_contains_tool_call(chunk: dict[str, Any]) -> bool:
    """Check if a chunk contains tool call data.

    Args:
        chunk: Chunk dict (converted from ModelResponse)

    Returns:
        True if chunk has tool call data in delta or message
    """
    choices = chunk.get("choices", [])
    if not choices:
        return False

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return False

    # Check delta for tool call data
    delta = first_choice.get("delta", {})
    if isinstance(delta, dict) and delta.get("tool_calls"):
        return True

    # Check message for tool call data
    message = first_choice.get("message", {})
    if isinstance(message, dict) and message.get("tool_calls"):
        return True

    return False


def is_tool_call_complete(chunk: dict[str, Any]) -> bool:
    """Check if a chunk indicates tool call completion.

    Args:
        chunk: Chunk dict (converted from ModelResponse)

    Returns:
        True if tool call is complete (finish_reason="tool_calls" or message has tool_calls)
    """
    choices = chunk.get("choices", [])
    if not choices:
        return False

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return False

    # Check for finish_reason == "tool_calls"
    finish_reason = first_choice.get("finish_reason")
    if finish_reason == "tool_calls":
        return True

    # Check for complete tool calls in message
    message = first_choice.get("message", {})
    if isinstance(message, dict) and message.get("tool_calls"):
        return True

    return False


__all__ = [
    "extract_tool_calls_from_response",
    "chunk_contains_tool_call",
    "is_tool_call_complete",
]
