"""Conversation Event Persistence.

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
from typing import Awaitable, Literal, Sequence

from pydantic import BaseModel, Field

from luthien_proxy.types import JSONObject, JSONValue
from luthien_proxy.utils import db, redis_client

logger = logging.getLogger(__name__)


class ConversationEvent(BaseModel):
    """Normalized conversation event (request or response)."""

    call_id: str
    event_type: Literal["request", "response"]
    timestamp: datetime
    hook: str  # Source hook that created this event
    payload: JSONObject = Field(default_factory=dict)


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


def build_conversation_events(
    hook: str,
    call_id: str | None,
    original: JSONValue | None,
    result: JSONValue | None,
    timestamp: datetime,
) -> list[ConversationEvent]:
    """Translate a hook invocation request/response into conversation events.

    Args:
        hook: Hook name identifying the event source
        call_id: Unique identifier for the request/response pair
        original: Original (pre-policy) data
        result: Final (post-policy) data
        timestamp: Event timestamp as datetime

    Returns:
        List of ConversationEvent objects (usually 0 or 1)
    """
    if not isinstance(call_id, str) or not call_id:
        return []

    events: list[ConversationEvent] = []

    # request event (from emit_request_event in storage/events.py)
    if hook == "request":
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
                event_type="request",
                timestamp=timestamp,
                hook=hook,
                payload=request_event_payload,  # type: ignore[arg-type]
            )
        )
        return events

    # response event (from emit_response_event in v2/storage/events.py)
    if hook == "response":
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
            "status": "success",
        }

        events.append(
            ConversationEvent(
                call_id=call_id,
                event_type="response",
                timestamp=timestamp,
                hook=hook,
                payload=response_event_payload,  # type: ignore[arg-type]
            )
        )
        return events

    return events


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
        INSERT INTO conversation_events (call_id, event_type, payload, created_at)
        VALUES ($1, $2, $3, $4)
        """,
        event.call_id,
        event.event_type,
        json.dumps(event.payload),
        timestamp_naive,
    )


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
