# ABOUTME: Shared utilities for policy implementations
# ABOUTME: Provides dataclasses and helpers used across multiple policies

"""Common utilities for policy implementations.

This module contains shared dataclasses, helpers, and utility functions
that are used by multiple policy implementations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

from litellm.types.utils import Choices, Message, ModelResponse


@dataclass(frozen=True)
class JudgeConfig:
    """Configuration for LLM judge.

    Attributes:
        model: LLM model identifier
        api_base: API base URL (optional)
        api_key: API key for authentication (optional)
        probability_threshold: Threshold for blocking (0-1)
        temperature: Sampling temperature for judge
        max_tokens: Maximum output tokens for judge response
    """

    model: str
    api_base: str | None
    api_key: str | None
    probability_threshold: float
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class JudgeResult:
    """Result from LLM judge evaluation."""

    probability: float
    explanation: str
    prompt: list[dict[str, str]]
    response_text: str


def create_text_response(text: str, model: str = "luthien-policy") -> ModelResponse:
    """Create a complete (non-streaming) text response.

    Args:
        text: The text content for the response
        model: Model name to include in response (default: "luthien-policy")

    Returns:
        A complete ModelResponse with the text content
    """
    # Use time + random for unique IDs
    import random

    unique_id = f"policy-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"

    return ModelResponse(
        id=unique_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=Message(content=text, role="assistant"),
            )
        ],
        created=int(time.time()),
        model=model,
        object="chat.completion",
    )


def create_text_chunk(text: str, model: str = "luthien-policy", finish_reason: str | None = None) -> ModelResponse:
    """Create a streaming text chunk.

    Args:
        text: The text content for the chunk
        model: Model name to include in chunk (default: "luthien-policy")
        finish_reason: Optional finish reason (e.g., "stop", "tool_calls")

    Returns:
        A ModelResponse chunk with the text content
    """
    # Use time + monotonic counter for unique IDs
    import random

    unique_id = f"policy-chunk-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"

    return ModelResponse(
        id=unique_id,
        choices=[
            Choices(
                index=0,
                delta={"content": text} if text else {},
                finish_reason=finish_reason,
            )
        ],
        created=int(time.time()),
        model=model,
        object="chat.completion.chunk",
    )


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
    "JudgeConfig",
    "JudgeResult",
    "create_text_response",
    "create_text_chunk",
    "extract_tool_calls_from_response",
    "chunk_contains_tool_call",
    "is_tool_call_complete",
]
