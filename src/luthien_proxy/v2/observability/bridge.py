# ABOUTME: Redis pub/sub bridge for real-time UI event monitoring
# ABOUTME: Publishes simplified events to maintain compatibility with /v2/activity/monitor

"""Event bridge for real-time UI monitoring.

This module provides a simplified event publisher that maintains compatibility
with the real-time activity monitor (/v2/activity/monitor) while we migrate to
OpenTelemetry for distributed tracing.

The SimpleEventPublisher sends minimal JSON events over Redis pub/sub to keep
the real-time UI working without the complexity of the old event system.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class SimpleEventPublisher:
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
        self.channel = "luthien:activity"

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
        from datetime import UTC, datetime

        event: dict[str, Any] = {
            "call_id": call_id,
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        if data:
            event["data"] = data  # type: ignore[assignment]

        try:
            await self.redis.publish(self.channel, json.dumps(event))
            logger.debug(f"Published event: {event_type} for call {call_id}")
        except Exception as e:
            logger.error(f"Failed to publish event to Redis: {e}")
            # Don't raise - event publishing failures shouldn't break requests


async def create_event_publisher(redis_url: str) -> SimpleEventPublisher:
    """Create and return a SimpleEventPublisher instance.

    Args:
        redis_url: Redis connection URL

    Returns:
        Configured SimpleEventPublisher
    """
    redis_client = await redis.from_url(redis_url)
    return SimpleEventPublisher(redis_client)
