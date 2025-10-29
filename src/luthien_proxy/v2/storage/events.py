# ABOUTME: Event emission helpers for V2 gateway to persist conversation events
# ABOUTME: Non-blocking queue-based persistence for conversation events

"""V2 Event Emission - Background persistence of conversation events.

This module provides non-blocking event emission for the V2 gateway.
Unlike V1 (which uses LiteLLM callbacks), V2 is an integrated architecture
so we build and emit events directly from the gateway layer.

Events are submitted to a background queue to avoid blocking the request path.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from luthien_proxy.v2.storage.persistence import (
    CONVERSATION_EVENT_QUEUE,
    build_conversation_events,
    publish_conversation_event,
    record_conversation_events,
)

if TYPE_CHECKING:
    from luthien_proxy.utils import db, redis_client

logger = logging.getLogger(__name__)


def emit_request_event(
    call_id: str,
    original_request: dict,
    final_request: dict,
    db_pool: db.DatabasePool | None,
    redis_conn: redis_client.RedisClient | None = None,
) -> None:
    """Emit request event to background queue (non-blocking).

    This builds a conversation event from the original (pre-policy) and
    final (post-policy) request, then submits it to the background queue
    for asynchronous persistence.

    Args:
        call_id: Unique identifier for this request/response cycle
        original_request: Request before policy transformation
        final_request: Request after policy transformation
        db_pool: Database connection pool for persistence
        redis_conn: Optional Redis connection for real-time pub/sub

    Note:
        This function returns immediately (fire-and-forget).
        Actual persistence happens in background via queue.
    """
    if not call_id:
        logger.error("emit_request_event called with empty call_id, skipping")
        return

    if db_pool is None:
        logger.debug(f"No db_pool provided for call {call_id}, skipping persistence")
        return

    # Build conversation events using V1's logic
    # Hook name "v2_request" distinguishes V2-emitted events
    events = build_conversation_events(
        hook="v2_request",
        call_id=call_id,
        trace_id=None,  # V2 doesn't use litellm trace_id
        original={"data": original_request},
        result={"data": final_request},
        timestamp_ns_fallback=time.time_ns(),
        timestamp=datetime.now(UTC),
    )

    if not events:
        logger.debug(f"No events generated for call {call_id} request")
        return

    # Submit to background queue (non-blocking)
    CONVERSATION_EVENT_QUEUE.submit(record_conversation_events(db_pool, events))

    # Optionally publish to Redis for real-time monitoring
    if redis_conn:
        for event in events:
            CONVERSATION_EVENT_QUEUE.submit(publish_conversation_event(redis_conn, event))

    logger.debug(f"Emitted request event for call {call_id} (queued for background persistence)")


def emit_response_event(
    call_id: str,
    original_response: dict,
    final_response: dict,
    db_pool: db.DatabasePool | None,
    redis_conn: redis_client.RedisClient | None = None,
) -> None:
    """Emit response event to background queue (non-blocking).

    This builds a conversation event from the original (pre-policy) and
    final (post-policy) response, then submits it to the background queue
    for asynchronous persistence.

    Args:
        call_id: Unique identifier for this request/response cycle
        original_response: Response before policy transformation
        final_response: Response after policy transformation
        db_pool: Database connection pool for persistence
        redis_conn: Optional Redis connection for real-time pub/sub

    Note:
        This function returns immediately (fire-and-forget).
        Actual persistence happens in background via queue.
    """
    if not call_id:
        logger.error("emit_response_event called with empty call_id, skipping")
        return

    if db_pool is None:
        logger.debug(f"No db_pool provided for call {call_id}, skipping persistence")
        return

    # Build conversation events using V1's logic
    # Hook name "v2_response" distinguishes V2-emitted events
    events = build_conversation_events(
        hook="v2_response",
        call_id=call_id,
        trace_id=None,  # V2 doesn't use litellm trace_id
        original={"response": original_response},
        result={"response": final_response},
        timestamp_ns_fallback=time.time_ns(),
        timestamp=datetime.now(UTC),
    )

    if not events:
        logger.debug(f"No events generated for call {call_id} response")
        return

    # Submit to background queue (non-blocking)
    CONVERSATION_EVENT_QUEUE.submit(record_conversation_events(db_pool, events))

    # Optionally publish to Redis for real-time monitoring
    if redis_conn:
        for event in events:
            CONVERSATION_EVENT_QUEUE.submit(publish_conversation_event(redis_conn, event))

    logger.debug(f"Emitted response event for call {call_id} (queued for background persistence)")


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


async def emit_custom_event(
    call_id: str,
    event_type: str,
    data: dict,
    db_pool: db.DatabasePool | None,
) -> None:
    """Emit custom event for observability.

    Args:
        call_id: Unique identifier for the transaction
        event_type: Type of event being emitted
        data: Event data (already enriched with call_id, trace_id, etc.)
        db_pool: Database connection pool for persistence
    """
    if not call_id:
        logger.error("emit_custom_event called with empty call_id, skipping")
        return

    if db_pool is None:
        logger.debug(f"No db_pool provided for call {call_id}, skipping persistence")
        return

    # Build conversation events using existing logic
    events = build_conversation_events(
        hook=event_type,
        call_id=call_id,
        trace_id=data.get("trace_id"),
        original={"data": data},
        result={"data": data},
        timestamp_ns_fallback=time.time_ns(),
        timestamp=datetime.now(UTC),
    )

    if not events:
        logger.debug(f"No events generated for call {call_id} custom event {event_type}")
        return

    # Submit to background queue (non-blocking)
    CONVERSATION_EVENT_QUEUE.submit(record_conversation_events(db_pool, events))

    logger.debug(f"Emitted custom event {event_type} for call {call_id}")


__all__ = [
    "emit_request_event",
    "emit_response_event",
    "emit_custom_event",
    "reconstruct_full_response_from_chunks",
]
