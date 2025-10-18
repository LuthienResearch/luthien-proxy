# ABOUTME: Publisher for V2 activity events to Redis.
# ABOUTME: Handles serialization and publishing to Redis pub/sub for livestream UI.
"""Publish V2 activity events to Redis."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from redis.asyncio import Redis

from luthien_proxy.v2.control.models import PolicyEvent

from .events import ActivityEvent, PolicyEventEmitted

logger = logging.getLogger(__name__)

# Redis channel for V2 activity stream
V2_ACTIVITY_CHANNEL = "luthien:v2:activity"


class ActivityPublisher:
    """Publishes activity events to Redis for real-time monitoring.

    This class handles both:
    1. Direct ActivityEvent publishing (structured events for UI)
    2. PolicyEvent handling (converts to PolicyEventEmitted and publishes)
    """

    def __init__(self, redis_client: Redis | None):
        """Initialize publisher with optional Redis client.

        Args:
            redis_client: Redis client for publishing. If None, events are logged but not published.
        """
        self.redis = redis_client

    def handle_policy_event(self, event: PolicyEvent) -> None:
        """Handle a policy event emission.

        This is the callback provided to PolicyContext. It:
        1. Logs the event to console
        2. Converts to PolicyEventEmitted ActivityEvent
        3. Publishes to Redis (async, non-blocking)

        Args:
            event: Policy event to handle
        """
        # Log to console
        logger.info(
            f"[{event.severity.upper()}] {event.event_type}: {event.summary}",
            extra={"call_id": event.call_id, "details": event.details},
        )

        # Convert to ActivityEvent and publish
        # Note: We create a task but don't await it to avoid blocking the policy
        activity_event = PolicyEventEmitted(
            call_id=event.call_id,
            trace_id=None,  # TODO: Get from context/metadata
            policy_name=event.event_type.split("_")[0],  # Extract from event type
            event_name=event.event_type,
            description=event.summary,
            data=event.details,
            phase="request",  # TODO: Track current phase in context
        )
        asyncio.create_task(self.publish(activity_event))

    async def publish(self, event: ActivityEvent) -> None:
        """Publish an activity event to Redis.

        Args:
            event: Activity event to publish
        """
        if self.redis is None:
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
        if self.redis is None:
            logger.debug("Activity publisher disabled (no Redis client)")
            return

        try:
            payload = json.dumps(data)
            await self.redis.publish(V2_ACTIVITY_CHANNEL, payload)
            logger.debug("Published raw JSON event")
        except Exception as exc:
            logger.error("Failed to publish raw JSON event: %s", exc)
