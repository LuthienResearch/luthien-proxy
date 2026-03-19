"""Event publisher protocol and in-process implementation.

Defines the interface for event publishing (pub/sub) and provides an
in-process implementation using asyncio queues. For local single-process
mode where Redis is not available.

Shared SSE helpers (build_activity_event, format_sse_payload, etc.) live here
so both RedisEventPublisher and InProcessEventPublisher can use them without
importing private symbols across modules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# --- Shared SSE helpers (used by both Redis and in-process publishers) ---


def build_activity_event(
    call_id: str,
    event_type: str,
    data: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build activity event dict for publication."""
    event: dict[str, Any] = {
        "call_id": call_id,
        "event_type": event_type,
        "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
    }
    if data:
        event["data"] = data
    return event


def format_sse_payload(payload: str) -> str:
    """Wrap a JSON string in SSE data frame format."""
    return f"data: {payload}\n\n"


def heartbeat_event() -> str:
    """Build an SSE heartbeat event with current timestamp."""
    return f"event: heartbeat\ndata: {json.dumps({'timestamp': time.time()})}\n\n"


def should_send_heartbeat(last_heartbeat: float, heartbeat_seconds: float) -> bool:
    """Check whether enough time has elapsed to send a heartbeat."""
    return time.monotonic() - last_heartbeat >= heartbeat_seconds


# --- Protocol ---


@runtime_checkable
class EventPublisherProtocol(Protocol):
    """Protocol for event publishing and streaming."""

    async def publish_event(
        self,
        call_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Publish an event to all subscribers."""
        ...

    def stream_events(
        self,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncGenerator[str, None]:
        """Async generator yielding SSE-formatted event strings."""
        ...


# --- In-process implementation ---


class InProcessEventPublisher:
    """In-process event publisher using asyncio queues.

    For single-process local mode. Each SSE subscriber gets its own queue;
    publish_event pushes to all subscriber queues.
    """

    def __init__(self) -> None:
        """Initialize with empty subscriber set."""
        self._subscribers: set[asyncio.Queue[str]] = set()

    async def publish_event(
        self,
        call_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Publish an event to all subscriber queues."""
        event = build_activity_event(call_id, event_type, data)
        payload = format_sse_payload(json.dumps(event))

        dead_queues: list[asyncio.Queue[str]] = []
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead_queues.append(queue)
                logger.warning("Dropping slow event subscriber")

        for q in dead_queues:
            self._subscribers.discard(q)

    async def stream_events(
        self,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncGenerator[str, None]:
        """Stream SSE events, yielding heartbeats when idle."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        last_heartbeat = time.monotonic()

        try:
            while True:
                if should_send_heartbeat(last_heartbeat, heartbeat_seconds):
                    last_heartbeat = time.monotonic()
                    yield heartbeat_event()
                    continue

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            self._subscribers.discard(queue)
