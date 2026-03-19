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
from typing import Any, AsyncGenerator, cast

import redis.asyncio as redis
from redis.asyncio.client import PubSub

from luthien_proxy.observability.event_publisher import (
    build_activity_event,
    format_sse_payload,
    heartbeat_event,
    should_send_heartbeat,
)
from luthien_proxy.utils.constants import (
    HEARTBEAT_INTERVAL_SECONDS,
    REDIS_POLL_TIMEOUT_BUFFER_SECONDS,
    REDIS_PUBSUB_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# Redis channel for activity events (used by both publisher and streamer)
ACTIVITY_CHANNEL = "luthien:activity"


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
        self.channel = ACTIVITY_CHANNEL

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

    async def stream_events(
        self,
        heartbeat_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> AsyncGenerator[str, None]:
        """Stream activity events as SSE. Satisfies EventPublisherProtocol."""
        async for event in stream_activity_events(
            self.redis,
            heartbeat_seconds=heartbeat_seconds,
        ):
            yield event


def _decode_payload(message: dict[str, Any]) -> str:
    payload = message["data"]
    return cast(bytes, payload).decode("utf-8")


async def _poll_pubsub_message(pubsub: PubSub, timeout_seconds: float) -> dict[str, Any] | None:
    try:
        return await asyncio.wait_for(
            pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout_seconds),
            timeout=timeout_seconds + REDIS_POLL_TIMEOUT_BUFFER_SECONDS,
        )
    except asyncio.TimeoutError:
        return None


async def stream_activity_events(
    redis_client: redis.Redis,
    heartbeat_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
    timeout_seconds: float = REDIS_PUBSUB_TIMEOUT_SECONDS,
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
        await pubsub.subscribe(ACTIVITY_CHANNEL)
        last_heartbeat = time.monotonic()

        logger.info("Started streaming activity events")

        try:
            while True:
                if should_send_heartbeat(last_heartbeat, heartbeat_seconds):
                    last_heartbeat = time.monotonic()
                    yield heartbeat_event()
                    continue

                message = await _poll_pubsub_message(pubsub, timeout_seconds)
                if not message or message.get("type") != "message":
                    # Timeout or subscription bookkeeping event
                    continue

                payload = _decode_payload(message)

                yield format_sse_payload(payload)

        except asyncio.CancelledError:
            logger.info("Activity stream cancelled by client")
            raise
        except Exception as exc:
            logger.error(f"Error in activity stream: {exc}")
            error_data = {"error": str(exc), "type": type(exc).__name__}
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
