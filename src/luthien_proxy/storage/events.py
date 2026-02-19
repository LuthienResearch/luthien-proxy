"""V2 Event Emission - Background persistence of conversation events.

This module provides non-blocking event emission for the V2 gateway.
Unlike V1 (which uses LiteLLM callbacks), V2 is an integrated architecture
so we build and emit events directly from the gateway layer.

Events are submitted to a background queue to avoid blocking the request path.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def reconstruct_full_response_from_chunks(chunks: list) -> dict:
    """Reconstruct a full response dict from streaming chunks.

    This function accumulates content from all streaming chunks and builds
    a synthetic response dict that matches the structure of a FullResponse.

    Args:
        chunks: List of StreamingResponse objects (wrapping LiteLLM chunks)

    Returns:
        Dict with structure matching FullResponse.to_model_response().model_dump()
        Contains: id, choices, model, usage, etc.

    Note:
        - Handles both wrapped StreamingResponse and raw chunk objects
        - Gracefully handles missing fields (returns minimal valid structure)
        - Accumulates content from all delta.content fields
    """
    if not chunks:
        # Return minimal valid structure
        return {
            "id": "",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "model": "",
            "usage": None,
        }

    # Accumulate content
    accumulated_content = []
    model_name = ""
    response_id = ""
    finish_reason = "stop"

    for chunk_wrapper in chunks:
        # Unwrap if it's a StreamingResponse, otherwise use directly
        chunk = chunk_wrapper.chunk if hasattr(chunk_wrapper, "chunk") else chunk_wrapper

        # Try to extract metadata from first chunk
        if not response_id and hasattr(chunk, "id"):
            response_id = chunk.id or ""
        if not model_name and hasattr(chunk, "model"):
            model_name = chunk.model or ""

        # Extract content from delta
        if hasattr(chunk, "choices") and chunk.choices:
            choice = chunk.choices[0]
            if hasattr(choice, "delta") and choice.delta:
                content = getattr(choice.delta, "content", None)
                if content:
                    accumulated_content.append(content)

            # Capture finish_reason from final chunk
            if hasattr(choice, "finish_reason") and choice.finish_reason:
                finish_reason = choice.finish_reason

    # Build synthetic response matching FullResponse structure
    return {
        "id": response_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(accumulated_content),
                },
                "finish_reason": finish_reason,
            }
        ],
        "model": model_name,
        "usage": None,  # Streaming chunks typically don't include usage
    }


__all__ = [
    "reconstruct_full_response_from_chunks",
]
