"""Redis-backed streaming helpers for conversation events."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

from luthien_proxy.utils import redis_client
from luthien_proxy.utils.project_config import ConversationStreamConfig

from .models import ConversationEvent

logger = logging.getLogger(__name__)

_CONVERSATION_CHANNEL_PREFIX = "luthien:conversation:"
_DEFAULT_STREAM_CONFIG = ConversationStreamConfig(
    heartbeat_seconds=15.0,
    redis_poll_timeout_seconds=1.0,
    rate_limit_max_requests=60,
    rate_limit_window_seconds=60.0,
)


def conversation_channel(call_id: str) -> str:
    """Redis pub/sub channel name for a specific call."""
    return f"{_CONVERSATION_CHANNEL_PREFIX}{call_id}"


async def publish_conversation_event(
    redis: redis_client.RedisClient,
    event: ConversationEvent,
) -> None:
    """Publish a conversation event on the per-call channel."""
    if not event.call_id:
        return
    try:
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to serialize conversation event: %s", exc)
        return
    try:
        await redis.publish(conversation_channel(event.call_id), payload)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to publish conversation event: %s", exc)


async def _stream_from_channel(
    redis: redis_client.RedisClient,
    channel: str,
    config: Optional[ConversationStreamConfig] = None,
) -> AsyncGenerator[str, None]:
    """Internal helper yielding SSE frames for a redis pub/sub channel."""
    settings = config or _DEFAULT_STREAM_CONFIG
    heartbeat_interval = max(0.5, settings.heartbeat_seconds)
    timeout = max(0.1, settings.redis_poll_timeout_seconds)
    async with redis.pubsub() as pubsub:
        await pubsub.subscribe(channel)
        last_heartbeat = time.monotonic()
        try:
            backoff = 0.1
            while True:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=timeout,
                    )
                    backoff = 0.1
                except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
                    raise
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.error(
                        "Conversation stream poll error on %s: %s; retrying in %.1fs",
                        channel,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10)
                    continue

                now = time.monotonic()
                if message is None:
                    if now - last_heartbeat >= heartbeat_interval:
                        last_heartbeat = now
                        yield ": ping\n\n"
                    continue

                data = message.get("data")
                if isinstance(data, bytes):
                    text = data.decode("utf-8", errors="ignore")
                else:
                    text = str(data)
                last_heartbeat = now
                yield f"data: {text}\n\n"
        finally:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("Failed to unsubscribe from %s", channel, exc_info=True)


async def conversation_sse_stream(
    redis: redis_client.RedisClient,
    call_id: str,
    config: Optional[ConversationStreamConfig] = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE data for a call-level channel."""
    channel = conversation_channel(call_id)
    async for chunk in _stream_from_channel(redis, channel, config=config):
        yield chunk


__all__ = [
    "publish_conversation_event",
    "conversation_sse_stream",
    "conversation_channel",
    "ConversationStreamConfig",
]
