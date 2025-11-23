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
from uuid import uuid4

from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Delta,
    Message,
    ModelResponse,
    StreamingChoices,
)


def create_text_response(
    text: str, model: str = "luthien-policy", response_id: str | None = None, finish_reason: str = "stop"
) -> ModelResponse:
    """Create a complete (non-streaming) text response.

    Args:
        text (str): The text content for the response
        model (str): Model name to include in response (default: "luthien-policy")
        response_id (str): Optional custom ID for the response (default: 'response-{uuid4()}')
        finish_reason (str): Finish reason for the response (default: "stop")

    Returns:
        A complete ModelResponse with the text content
    """
    unique_id = response_id or f"response-{uuid4()}"

    return ModelResponse(
        id=unique_id,
        choices=[
            Choices(
                finish_reason=finish_reason,
                index=0,
                message=Message(content=text, role="assistant"),
            )
        ],
        created=int(time.time()),
        model=model,
        object="chat.completion",
    )


def create_text_chunk(
    text: str, model: str = "luthien-policy", response_id: str | None = None, finish_reason: str | None = None
) -> ModelResponse:
    """Create a streaming text chunk.

    Args:
        text (str): The text content for the chunk
        model (str): Model name to include in chunk (default: "luthien-policy")
        response_id (str): Optional custom ID for the chunk (default: 'response-chunk-{uuid4()}')
        finish_reason (str): Optional finish reason (e.g., "stop", "tool_calls")

    Returns:
        A ModelResponse chunk with the text content
    """
    unique_id = response_id or f"response-chunk-{uuid4()}"

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


def create_tool_call_chunk(
    tool_call: ChatCompletionMessageToolCall,
    model: str = "luthien-policy",
    finish_reason: str | None = None,
) -> ModelResponse:
    """Create a streaming chunk with a complete tool call.

    Args:
        tool_call: ChatCompletionMessageToolCall object from litellm
        model: Model name to include in chunk (default: "luthien-policy")
        finish_reason: Optional finish reason. Should only be set on the last tool call
            in a multi-tool-call response (e.g., "tool_calls").

    Returns:
        A ModelResponse chunk with the tool call
    """
    unique_id = f"policy-tool-{uuid4()}"

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
            StreamingChoices(
                index=0,
                delta=Delta(tool_calls=[tool_call_dict]),
                finish_reason=finish_reason,
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
