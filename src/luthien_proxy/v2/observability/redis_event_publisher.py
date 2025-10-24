# ABOUTME: Redis pub/sub bridge for real-time UI event monitoring
# ABOUTME: Publishes and streams events for /v2/activity/monitor compatibility

"""Event bridge for real-time UI monitoring.

This module provides both publishing and streaming of activity events via Redis pub/sub:
- SimpleEventPublisher: Publishes events to Redis for real-time monitoring
- stream_activity_events: SSE endpoint that streams events to monitoring UI

This maintains compatibility with the real-time activity monitor while we use
OpenTelemetry for distributed tracing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, AsyncGenerator

import redis.asyncio as redis
from redis.asyncio.client import PubSub

logger = logging.getLogger(__name__)

# Redis channel for activity events (used by both publisher and streamer)
V2_ACTIVITY_CHANNEL = "luthien:activity"


def build_activity_event(
    call_id: str,
    event_type: str,
    data: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build activity event dict for Redis publication.

    This is a pure function that constructs event dictionaries without side effects,
    making it easily unit testable without requiring Redis infrastructure.

    Args:
        call_id: Unique request identifier
        event_type: Event type (e.g., "policy.content_filtered")
        data: Optional event-specific data
        timestamp: Optional timestamp (defaults to now)

    Returns:
        Event dict ready for JSON serialization
    """
    event: dict[str, Any] = {
        "call_id": call_id,
        "event_type": event_type,
        "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
    }
    if data:
        event["data"] = data
    return event


class RedisEventPublisher:
    """Simplified event publisher for real-time UI via Redis pub/sub.

    This publisher sends lightweight JSON events to Redis for consumption by
    the real-time activity monitor UI. It's designed to be a thin bridge that
    keeps the UI working while OpenTelemetry handles detailed tracing.

    Events are published to the "luthien:activity" channel in this format:
    {
        "call_id": "abc123",
        "event_type": "policy.content_filtered",
        "timestamp": "2024-01-15T10:30:00Z",
        "data": {...}  // Optional event-specific data
    }
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        """Initialize the event publisher.

        Args:
            redis_client: Async Redis client for pub/sub
        """
        self.redis = redis_client
        self.channel = V2_ACTIVITY_CHANNEL

    async def publish_event(
        self,
        call_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Publish a simplified event to Redis for real-time UI.

        Args:
            call_id: Unique request identifier (correlates with trace_id)
            event_type: Event type (e.g., "policy.content_filtered")
            data: Optional event-specific data
        """
        event = build_activity_event(call_id, event_type, data)

        try:
            await self.redis.publish(self.channel, json.dumps(event))
            logger.debug(f"Published event: {event_type} for call {call_id}")
        except Exception as e:
            logger.error(f"Failed to publish event to Redis: {e}")
            # Don't raise - event publishing failures shouldn't break requests


async def create_event_publisher(redis_url: str) -> RedisEventPublisher:
    """Create and return a SimpleEventPublisher instance.

    Args:
        redis_url: Redis connection URL

    Returns:
        Configured SimpleEventPublisher
    """
    redis_client = await redis.from_url(redis_url)
    return RedisEventPublisher(redis_client)


def _should_send_heartbeat(last_heartbeat: float, heartbeat_seconds: float) -> bool:
    return time.monotonic() - last_heartbeat >= heartbeat_seconds


def _heartbeat_event() -> str:
    return f"event: heartbeat\ndata: {json.dumps({'timestamp': time.time()})}\n\n"


def _format_sse_payload(payload: str) -> str:
    return f"data: {payload}\n\n"


def _decode_payload(message: dict[str, Any]) -> str:
    payload = message.get("data")
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    elif isinstance(payload, str):
        return payload
    else:
        raise ValueError(f"Unexpected payload type: {type(payload)}")


async def _poll_pubsub_message(pubsub: PubSub, timeout_seconds: float) -> dict[str, Any] | None:
    try:
        return await asyncio.wait_for(
            pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout_seconds),
            timeout=timeout_seconds + 0.5,
        )
    except asyncio.TimeoutError:
        return None


async def stream_activity_events(
    redis_client: redis.Redis,
    heartbeat_seconds: float = 15.0,
    timeout_seconds: float = 1.0,
) -> AsyncGenerator[str, None]:
    r"""Stream activity events as Server-Sent Events.

    Args:
        redis_client: Redis client for pub/sub
        heartbeat_seconds: How often to send keepalive heartbeats
        timeout_seconds: Redis pub/sub poll timeout

    Yields:
        SSE-formatted strings (data: {...}\\n\\n)
    """
    async with redis_client.pubsub() as pubsub:
        await pubsub.subscribe(V2_ACTIVITY_CHANNEL)
        last_heartbeat = time.monotonic()

        logger.info("Started streaming V2 activity events")

        try:
            while True:
                if _should_send_heartbeat(last_heartbeat, heartbeat_seconds):
                    last_heartbeat = time.monotonic()
                    yield _heartbeat_event()
                    continue

                message = await _poll_pubsub_message(pubsub, timeout_seconds)
                if not message or message.get("type") != "message":
                    # Timeout or subscription bookkeeping event
                    continue

                payload = _decode_payload(message)

                yield _format_sse_payload(payload)

        except asyncio.CancelledError:
            logger.info("Activity stream cancelled by client")
            raise
        except Exception as exc:
            logger.error(f"Error in activity stream: {exc}")
            error_data = {"error": str(exc), "type": type(exc).__name__}
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
