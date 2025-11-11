# ABOUTME: Database persistence for conversation events in V2
# ABOUTME: Self-contained module extracted from V1 control_plane to minimize dependencies

"""V2 Conversation Event Persistence.

This module provides database persistence for V2 conversation events.
It's a minimal extraction from the V1 control_plane conversation infrastructure,
containing only what V2 needs for storing request/response pairs.

Key components:
- ConversationEvent model
- build_conversation_events() - creates events from request/response data
- record_conversation_events() - persists events to database
- publish_conversation_event() - publishes events to Redis for real-time monitoring
- SequentialTaskQueue - background task queue for non-blocking persistence
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Awaitable, Literal, Sequence, cast

from pydantic import BaseModel, Field

from luthien_proxy.types import JSONObject, JSONValue
from luthien_proxy.utils import db, redis_client

logger = logging.getLogger(__name__)

# ============================================================================
# Models
# ============================================================================


class ConversationEvent(BaseModel):
    """Normalized conversation event (request or response)."""

    call_id: str
    trace_id: str | None = None  # Deprecated, always None
    event_type: Literal["request", "response"]
    sequence: int
    timestamp: datetime
    hook: str  # Source hook that created this event
    payload: JSONObject = Field(default_factory=dict)


# ============================================================================
# Task Queue for Background Persistence
# ============================================================================


class SequentialTaskQueue:
    """Process submitted awaitables one-by-one in FIFO order."""

    def __init__(self, name: str) -> None:
        """Initialise an empty queue bound to *name* for logging."""
        self._name = name
        self._queue: asyncio.Queue[Awaitable[None]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._logger = logging.getLogger(__name__)

    def submit(self, coro: Awaitable[None]) -> None:
        """Schedule *coro* to run after previously queued tasks."""
        loop = asyncio.get_running_loop()
        self._queue.put_nowait(coro)
        if self._worker is None or self._worker.done():
            self._worker = loop.create_task(self._drain())

    async def _drain(self) -> None:
        """Run queued coroutines sequentially until the queue is empty."""
        while True:
            try:
                coro = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await coro
            except Exception as exc:  # pragma: no cover - diagnostic path
                self._logger.error("SequentialTaskQueue[%s] task failed: %s", self._name, exc)
            finally:
                self._queue.task_done()


# Global queue for conversation event persistence
CONVERSATION_EVENT_QUEUE = SequentialTaskQueue("conversation_events")


# ============================================================================
# Utility Functions for Event Building
# ============================================================================


def require_dict(value: object, context: str) -> JSONObject:
    """Ensure *value* is a dict, raising a descriptive error otherwise."""
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a dict; saw {type(value)!r}")
    if not all(isinstance(key, str) for key in value.keys()):
        raise ValueError(f"{context} must use string keys; saw {list(value.keys())!r}")
    return cast(JSONObject, value)


def extract_post_time_ns_from_any(value: object) -> int | None:
    """Search arbitrarily nested data for a `post_time_ns` integer."""
    if isinstance(value, dict):
        candidate = value.get("post_time_ns")
        if isinstance(candidate, (int, float)):
            return int(candidate)
        for key in ("payload", "data", "request_data", "response", "response_obj", "raw_response", "chunk"):
            if key in value:
                nested = extract_post_time_ns_from_any(value.get(key))
                if nested is not None:
                    return nested
        for nested_value in value.values():
            if isinstance(nested_value, (dict, list)):
                nested = extract_post_time_ns_from_any(nested_value)
                if nested is not None:
                    return nested
    elif isinstance(value, list):
        for item in value:
            nested = extract_post_time_ns_from_any(item)
            if nested is not None:
                return nested
    return None


def derive_sequence_ns(fallback_ns: int, *candidates: object) -> int:
    """Pick the first available `post_time_ns`, falling back to *fallback_ns*."""
    for candidate in candidates:
        ns = extract_post_time_ns_from_any(candidate)
        if ns is not None:
            return ns
    return fallback_ns


# ============================================================================
# Event Building
# ============================================================================


def build_conversation_events(
    hook: str,
    call_id: str | None,
    trace_id: str | None,
    original: JSONValue | None,
    result: JSONValue | None,
    timestamp_ns_fallback: int,
    timestamp: datetime,
) -> list[ConversationEvent]:
    """Translate a hook invocation (V1) or V2 request/response into conversation events.

    This function handles multiple hook types from V1, but V2 primarily uses:
    - "v2_request" for request events
    - "v2_response" for response events

    Args:
        hook: Hook name identifying the event source
        call_id: Unique identifier for the request/response pair
        trace_id: Deprecated, always None
        original: Original (pre-policy) data
        result: Final (post-policy) data
        timestamp_ns_fallback: Timestamp in nanoseconds for sequence ordering
        timestamp: Event timestamp as datetime

    Returns:
        List of ConversationEvent objects (usually 0 or 1)
    """
    if not isinstance(call_id, str) or not call_id:
        return []

    sequence_ns = derive_sequence_ns(timestamp_ns_fallback, original, result)
    events: list[ConversationEvent] = []

    # V2 request event (from emit_request_event in v2/storage/events.py)
    if hook == "v2_request":
        if not isinstance(original, dict) or not isinstance(result, dict):
            return []

        original_data = original.get("data")
        result_data = result.get("data")

        if not isinstance(original_data, dict) or not isinstance(result_data, dict):
            return []

        original_request = {
            "messages": original_data.get("messages", []),
            "model": original_data.get("model"),
            "temperature": original_data.get("temperature"),
            "max_tokens": original_data.get("max_tokens"),
            "tools": original_data.get("tools"),
            "tool_choice": original_data.get("tool_choice"),
        }
        original_request = {k: v for k, v in original_request.items() if v is not None}

        final_request = {
            "messages": result_data.get("messages", []),
            "model": result_data.get("model"),
            "temperature": result_data.get("temperature"),
            "max_tokens": result_data.get("max_tokens"),
            "tools": result_data.get("tools"),
            "tool_choice": result_data.get("tool_choice"),
        }
        final_request = {k: v for k, v in final_request.items() if v is not None}

        request_event_payload = {
            "original": original_request,
            "final": final_request,
            # Top-level fields use final (post-policy) values for backwards compatibility
            "messages": final_request.get("messages", []),
            "model": final_request.get("model"),
            "temperature": final_request.get("temperature"),
            "max_tokens": final_request.get("max_tokens"),
            "tools": final_request.get("tools"),
            "tool_choice": final_request.get("tool_choice"),
        }

        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=None,
                event_type="request",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload=request_event_payload,  # type: ignore[arg-type]
            )
        )
        return events

    # V2 response event (from emit_response_event in v2/storage/events.py)
    if hook == "v2_response":
        if not isinstance(original, dict) or not isinstance(result, dict):
            return []

        original_response = original.get("response")
        final_response = result.get("response")

        if not isinstance(original_response, dict) or not isinstance(final_response, dict):
            return []

        response_event_payload = {
            "original": original_response,
            "final": final_response,
            # Top-level fields use final for backwards compatibility
            "choices": final_response.get("choices", []),
            "model": final_response.get("model"),
            "usage": final_response.get("usage"),
            "finish_reason": final_response.get("finish_reason"),
            "status": "success",  # V2 only emits successful responses
        }

        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=None,
                event_type="response",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload=response_event_payload,  # type: ignore[arg-type]
            )
        )
        return events

    # Legacy V1 hooks (keep minimal support for backward compatibility)
    # These are not used by V2 but kept to avoid breaking existing data
    if hook in ("async_pre_call_hook", "async_post_call_success_hook"):
        logger.debug(f"Skipping V1 hook: {hook} (V1 infrastructure removed)")
        return []

    return events


# ============================================================================
# Database Persistence
# ============================================================================


async def record_conversation_events(
    pool: db.DatabasePool | None,
    events: Sequence[ConversationEvent],
) -> None:
    """Persist the given events into the conversation tables."""
    if pool is None or not events:
        return

    async with pool.connection() as conn:
        for event in events:
            await _ensure_call_row(conn, event)

            if event.event_type == "request":
                await _apply_request_event(conn, event)
            elif event.event_type == "response":
                await _apply_response_event(conn, event)

            await _insert_event_row(conn, event)


async def _ensure_call_row(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    """Ensure a row exists for the call id."""
    # Strip timezone for postgres timestamp without time zone
    timestamp_naive = event.timestamp.replace(tzinfo=None) if event.timestamp.tzinfo else event.timestamp

    await conn.execute(
        """
        INSERT INTO conversation_calls (call_id, created_at)
        VALUES ($1, $2)
        ON CONFLICT (call_id) DO NOTHING
        """,
        event.call_id,
        timestamp_naive,
    )


async def _apply_request_event(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    """Update call metadata from request event."""
    payload = event.payload
    model_name = payload.get("model")
    timestamp_naive = event.timestamp.replace(tzinfo=None) if event.timestamp.tzinfo else event.timestamp

    await conn.execute(
        """
        UPDATE conversation_calls
        SET model_name = COALESCE(model_name, $2),
            status = 'started',
            created_at = LEAST(created_at, $3)
        WHERE call_id = $1
        """,
        event.call_id,
        model_name if isinstance(model_name, str) else None,
        timestamp_naive,
    )


async def _apply_response_event(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    """Update call metadata from response event."""
    payload = event.payload
    status = str(payload.get("status", "success"))
    timestamp_naive = event.timestamp.replace(tzinfo=None) if event.timestamp.tzinfo else event.timestamp

    await conn.execute(
        """
        UPDATE conversation_calls
        SET status = $2,
            completed_at = COALESCE(completed_at, $3)
        WHERE call_id = $1
        """,
        event.call_id,
        status,
        timestamp_naive,
    )


async def _insert_event_row(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    """Insert event row into conversation_events."""
    timestamp_naive = event.timestamp.replace(tzinfo=None) if event.timestamp.tzinfo else event.timestamp

    await conn.execute(
        """
        INSERT INTO conversation_events (
            call_id,
            event_type,
            sequence,
            payload,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5)
        """,
        event.call_id,
        event.event_type,
        int(event.sequence),
        json.dumps(event.payload),
        timestamp_naive,
    )


# ============================================================================
# Redis Publishing for Real-Time Monitoring
# ============================================================================


async def publish_conversation_event(
    redis: redis_client.RedisClient,
    event: ConversationEvent,
) -> None:
    """Publish a conversation event to Redis for real-time monitoring."""
    if not event.call_id:
        return

    channel = f"luthien:conversation:{event.call_id}"

    try:
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to serialize conversation event: %s", exc)
        return

    try:
        await redis.publish(channel, payload)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to publish conversation event: %s", exc)


__all__ = [
    "ConversationEvent",
    "CONVERSATION_EVENT_QUEUE",
    "build_conversation_events",
    "record_conversation_events",
    "publish_conversation_event",
]
