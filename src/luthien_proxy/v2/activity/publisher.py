# ABOUTME: Publisher for V2 activity events to Redis.
# ABOUTME: Handles serialization and publishing to Redis pub/sub for livestream UI.
"""Publish V2 activity events to Redis."""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

from .events import ActivityEvent

logger = logging.getLogger(__name__)

# Redis channel for V2 activity stream
V2_ACTIVITY_CHANNEL = "luthien:v2:activity"


class ActivityPublisher:
    """Publishes activity events to Redis for real-time monitoring."""

    def __init__(self, redis_client: Redis | None):
        """Initialize publisher with optional Redis client.

        Args:
            redis_client: Redis client for publishing. If None, events are logged but not published.
        """
        self.redis = redis_client
        self._enabled = redis_client is not None

    async def publish(self, event: ActivityEvent) -> None:
        """Publish an activity event to Redis.

        Args:
            event: Activity event to publish
        """
        if not self._enabled:
            logger.debug("Activity publisher disabled (no Redis client), event: %s", event.event_type)
            return

        try:
            # Serialize event to JSON
            payload = event.model_dump_json()

            # Publish to Redis channel
            await self.redis.publish(V2_ACTIVITY_CHANNEL, payload)

            logger.debug(
                "Published %s event for call_id=%s",
                event.event_type,
                event.call_id,
            )

        except Exception as exc:
            logger.error(
                "Failed to publish activity event (type=%s, call_id=%s): %s",
                event.event_type,
                event.call_id,
                exc,
            )

    async def publish_json(self, data: dict[str, Any]) -> None:
        """Publish raw JSON data to the activity stream.

        This is a fallback for events that don't fit the ActivityEvent model.

        Args:
            data: Dictionary to publish as JSON
        """
        if not self._enabled:
            logger.debug("Activity publisher disabled (no Redis client)")
            return

        try:
            payload = json.dumps(data)
            await self.redis.publish(V2_ACTIVITY_CHANNEL, payload)
            logger.debug("Published raw JSON event")
        except Exception as exc:
            logger.error("Failed to publish raw JSON event: %s", exc)
