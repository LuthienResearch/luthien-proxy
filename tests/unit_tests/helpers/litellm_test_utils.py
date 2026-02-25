
"""Test utilities for creating normalized litellm responses.

This module provides helper functions that mirror what litellm_client.py does in production:
- Production: litellm_client calls litellm.acompletion() and normalizes the response
- Tests: Use these helpers to create chunks that are normalized the same way

Tests should use these helpers instead of creating ModelResponse objects directly,
keeping test code isomorphic with production code.
"""

from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.llm.response_normalizer import normalize_chunk_with_finish_reason


def make_streaming_chunk(
    content: str | None,
    model: str = "gpt-4",
    id: str = "test-chunk-id",
    finish_reason: str | None = None,
    role: str = "assistant",
    tool_calls: list | None = None,
    reasoning_content: str | None = None,
) -> ModelResponse:
    """Create a normalized streaming chunk, as litellm_client.stream() would return.

    This mirrors the production flow where litellm returns a chunk and
    litellm_client normalizes it before returning to callers.

    Args:
        content: Text content for the delta
        model: Model name
        id: Response ID
        finish_reason: Finish reason (None for intermediate chunks, "stop" for final)
        role: Role for the delta (usually "assistant")
        tool_calls: Optional tool calls for the delta
        reasoning_content: Optional reasoning/thinking content

    Returns:
        Normalized ModelResponse chunk with Delta object (not dict) and correct finish_reason
    """
    delta = Delta(role=role, content=content, tool_calls=tool_calls)
    if reasoning_content is not None:
        delta.reasoning_content = reasoning_content

    raw_chunk = ModelResponse(
        id=id,
        created=1234567890,
        model=model,
        object="chat.completion.chunk",
        choices=[
            StreamingChoices(
                index=0,
                delta=delta,
                finish_reason=finish_reason,
            )
        ],
    )
    # Normalize to match what litellm_client.stream() returns
    return normalize_chunk_with_finish_reason(raw_chunk, finish_reason)


__all__ = ["make_streaming_chunk"]
