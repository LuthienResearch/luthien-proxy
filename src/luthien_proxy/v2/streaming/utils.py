# ABOUTME: Utility functions for building streaming response chunks
# ABOUTME: Provides helpers for creating text and block chunks from aggregated data

"""Utility functions for building streaming response chunks.

This module provides helper functions for constructing ModelResponse chunks
from text content or completed StreamBlocks. These utilities are used by
policies to build chunks when implementing custom streaming behavior.
"""

from __future__ import annotations

from typing import Any

from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.v2.streaming.stream_blocks import (
    ContentStreamBlock,
    StreamBlock,
    ToolCallStreamBlock,
)


def build_text_chunk(
    text: str,
    model: str,
    finish_reason: str | None = None,
) -> ModelResponse:
    """Build a text content chunk.

    Args:
        text: Text content
        model: Model name
        finish_reason: Optional finish reason ("stop", "length", etc.)

    Returns:
        ModelResponse chunk with text content
    """
    delta: dict[str, Any] = {}
    if text:
        delta["content"] = text

    choice = StreamingChoices(
        index=0,
        delta=Delta(**delta),
        finish_reason=finish_reason,
    )

    return ModelResponse(
        id="chatcmpl-generated",
        choices=[choice],
        created=0,
        model=model,
        object="chat.completion.chunk",
    )


def build_block_chunk(
    block: StreamBlock,
    model: str,
    finish_reason: str | None = None,
) -> ModelResponse:
    """Build a chunk from a completed StreamBlock.

    Args:
        block: ContentStreamBlock or ToolCallStreamBlock
        model: Model name
        finish_reason: Optional finish reason

    Returns:
        ModelResponse chunk with block data

    Raises:
        ValueError: If block type is not supported
    """
    if isinstance(block, ContentStreamBlock):
        # Build content chunk
        return build_text_chunk(block.content, model, finish_reason)

    elif isinstance(block, ToolCallStreamBlock):
        # Build tool call chunk with complete data
        delta: dict[str, Any] = {
            "tool_calls": [
                {
                    "index": block.index,
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": block.arguments,
                    },
                }
            ]
        }

        choice = StreamingChoices(
            index=0,
            delta=Delta(**delta),
            finish_reason=finish_reason,
        )

        return ModelResponse(
            id="chatcmpl-generated",
            choices=[choice],
            created=0,
            model=model,
            object="chat.completion.chunk",
        )

    else:
        raise ValueError(f"Unsupported block type: {type(block)}")


__all__ = [
    "build_text_chunk",
    "build_block_chunk",
]
