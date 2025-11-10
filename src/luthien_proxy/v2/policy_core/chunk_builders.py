# ABOUTME: Chunk builder utilities for creating ModelResponse chunks
# ABOUTME: Extracted from policies/utils.py to break circular dependency

"""Utilities for creating LiteLLM ModelResponse chunks.

This module provides helper functions for creating properly formatted
ModelResponse objects for both streaming chunks and complete responses.
These utilities are used by both policies and streaming modules to emit
chunks without needing to understand the full ModelResponse structure.
"""

from __future__ import annotations

import time
from typing import Any

from litellm.types.utils import Choices, Delta, ModelResponse, StreamingChoices


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
                message={"content": text, "role": "assistant"},
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

    # Create proper Delta object instead of dict for compatibility
    delta = Delta(content=text if text else None)

    # Use StreamingChoices for streaming chunks (not Choices which is for non-streaming)
    return ModelResponse(
        id=unique_id,
        choices=[
            StreamingChoices(
                index=0,
                delta=delta,
                finish_reason=finish_reason,
            )
        ],
        created=int(time.time()),
        model=model,
        object="chat.completion.chunk",
    )


def create_tool_call_chunk(tool_call: Any, model: str = "luthien-policy") -> ModelResponse:
    """Create a streaming chunk with a complete tool call.

    Args:
        tool_call: ChatCompletionMessageToolCall object from litellm
        model: Model name to include in chunk (default: "luthien-policy")

    Returns:
        A ModelResponse chunk with the tool call
    """
    import random

    unique_id = f"policy-tool-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"

    # Convert tool call to dict format expected in delta
    tool_call_dict = {
        "id": tool_call.id if hasattr(tool_call, "id") else "",
        "type": "function",
        "function": {
            "name": tool_call.function.name if hasattr(tool_call, "function") else "",
            "arguments": tool_call.function.arguments if hasattr(tool_call, "function") else "",
        },
    }

    return ModelResponse(
        id=unique_id,
        choices=[
            Choices(
                index=0,
                delta={"tool_calls": [tool_call_dict]},
                finish_reason="tool_calls",
            )
        ],
        created=int(time.time()),
        model=model,
        object="chat.completion.chunk",
    )


__all__ = [
    "create_text_response",
    "create_text_chunk",
    "create_tool_call_chunk",
]
