"""Normalize litellm responses for consistent downstream handling.

litellm >= 1.81.0 introduced breaking changes:
- StreamingChoices.delta returns dict instead of Delta object
- Choices defaults finish_reason to "stop" instead of None
- StreamingChoices passed to ModelResponse gets converted to Choices

This module provides normalization functions to restore expected behavior.
Call normalize_chunk() on streaming chunks received from litellm to ensure
delta is a Delta object and finish_reason is properly preserved.
"""

from collections.abc import AsyncIterator
from typing import Any, cast

from litellm.types.utils import Delta, ModelResponse, StreamingChoices


def normalize_chunk(chunk: ModelResponse) -> ModelResponse:
    """Normalize a streaming chunk to have proper Delta objects.

    Converts dict deltas to Delta objects for consistent attribute access.
    litellm >= 1.81.0 returns delta as a dict instead of Delta object.

    Args:
        chunk: Raw ModelResponse chunk from litellm

    Returns:
        Chunk with normalized Delta objects
    """
    if not chunk.choices:
        return chunk

    for choice in chunk.choices:
        # Cast to StreamingChoices to access delta (works for both Choices and StreamingChoices)
        streaming_choice = cast(StreamingChoices, choice)
        if hasattr(streaming_choice, "delta") and isinstance(streaming_choice.delta, dict):
            streaming_choice.delta = Delta(**streaming_choice.delta)

    return chunk


def normalize_chunk_with_finish_reason(chunk: ModelResponse, intended_finish_reason: str | None) -> ModelResponse:
    """Normalize a streaming chunk and restore the intended finish_reason.

    litellm >= 1.81.0 defaults finish_reason to "stop" instead of preserving None.
    This function normalizes delta objects AND restores the intended finish_reason.

    Use this for test fixtures where you know the intended finish_reason value.

    Args:
        chunk: Raw ModelResponse chunk
        intended_finish_reason: The finish_reason you actually wanted (often None for intermediate chunks)

    Returns:
        Chunk with normalized Delta objects and correct finish_reason
    """
    chunk = normalize_chunk(chunk)

    if chunk.choices:
        for choice in chunk.choices:
            choice.finish_reason = cast(Any, intended_finish_reason)

    return chunk


async def normalize_stream(
    stream: AsyncIterator[ModelResponse],
) -> AsyncIterator[ModelResponse]:
    """Wrap a litellm stream to normalize each chunk.

    Args:
        stream: Raw async iterator of ModelResponse chunks from litellm

    Yields:
        Normalized ModelResponse chunks with proper Delta objects
    """
    async for chunk in stream:
        yield normalize_chunk(chunk)


def normalize_response(response: ModelResponse) -> ModelResponse:
    """Normalize a non-streaming response.

    Currently a passthrough since non-streaming responses use Message not Delta.
    Included for completeness and future compatibility.

    Args:
        response: Raw ModelResponse from litellm

    Returns:
        Normalized ModelResponse
    """
    return response


__all__ = ["normalize_chunk", "normalize_chunk_with_finish_reason", "normalize_stream", "normalize_response"]
