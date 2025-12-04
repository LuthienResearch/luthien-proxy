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


def create_finish_chunk(
    finish_reason: str,
    model: str | None = "luthien-policy",
    chunk_id: str | None = None,
) -> ModelResponse:
    """Create a streaming chunk with only a finish_reason (empty delta).

    This is used to properly terminate tool call streams where individual tool call
    chunks should not have finish_reason set, and the final finish_reason="tool_calls"
    must be emitted as a separate chunk with an empty delta.

    Args:
        finish_reason: The finish reason (e.g., "stop", "tool_calls")
        model: Model name to include in chunk (default: "luthien-policy")
        chunk_id: Optional custom ID for the chunk (default: 'finish-{uuid4()}')

    Returns:
        A ModelResponse chunk with empty delta and the finish_reason set
    """
    unique_id = chunk_id or f"finish-{uuid4()}"

    return ModelResponse(
        id=unique_id,
        choices=[
            StreamingChoices(
                index=0,
                delta=Delta(content=None, role=None),
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
) -> ModelResponse:
    """Create a streaming chunk with a complete tool call.

    Tool call chunks should NOT include finish_reason - use create_finish_chunk()
    at the end of the stream instead.

    Args:
        tool_call: ChatCompletionMessageToolCall object from litellm
        model: Model name to include in chunk (default: "luthien-policy")

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
                finish_reason=None,
            )
        ],
        created=int(time.time()),
        model=model,
        object="chat.completion.chunk",
    )


__all__ = [
    "create_text_response",
    "create_text_chunk",
    "create_finish_chunk",
    "create_tool_call_chunk",
]
