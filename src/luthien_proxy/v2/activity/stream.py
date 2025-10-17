# ABOUTME: SSE streaming endpoint for real-time activity monitoring.
# ABOUTME: Provides Server-Sent Events stream of all V2 gateway activity for debugging UI.
"""Stream V2 activity events over Server-Sent Events."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

from redis.asyncio import Redis

from .publisher import V2_ACTIVITY_CHANNEL

logger = logging.getLogger(__name__)


async def stream_activity_events(
    redis_client: Redis,
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
                # Check if we need to send a heartbeat
                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_seconds:
                    yield f"event: heartbeat\ndata: {json.dumps({'timestamp': time.time()})}\n\n"
                    last_heartbeat = now

                # Try to get a message from Redis (with timeout)
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout_seconds),
                        timeout=timeout_seconds + 0.5,  # Add buffer to async timeout
                    )

                    if message and message["type"] == "message":
                        # Got an activity event - forward it to the client
                        payload = message["data"]
                        if isinstance(payload, bytes):
                            payload = payload.decode("utf-8")

                        yield f"data: {payload}\n\n"

                except asyncio.TimeoutError:
                    # No message within timeout - continue loop (will send heartbeat if needed)
                    continue

        except asyncio.CancelledError:
            logger.info("Activity stream cancelled by client")
            raise
        except Exception as exc:
            logger.error(f"Error in activity stream: {exc}")
            error_data = {"error": str(exc), "type": type(exc).__name__}
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
