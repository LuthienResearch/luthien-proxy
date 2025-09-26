"""Streaming helpers for conversation events."""

from __future__ import annotations

import json
import logging
import time
from typing import AsyncGenerator

from luthien_proxy.utils import redis_client

from .models import ConversationEvent

logger = logging.getLogger(__name__)

_CONVERSATION_CHANNEL_PREFIX = "luthien:conversation:"
_CONVERSATION_TRACE_CHANNEL_PREFIX = "luthien:conversation-trace:"


def conversation_channel(call_id: str) -> str:
    """Redis pub/sub channel name for a specific call."""
    return f"{_CONVERSATION_CHANNEL_PREFIX}{call_id}"


def conversation_trace_channel(trace_id: str) -> str:
    """Redis pub/sub channel name for a specific trace."""
    return f"{_CONVERSATION_TRACE_CHANNEL_PREFIX}{trace_id}"


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


async def publish_trace_conversation_event(
    redis: redis_client.RedisClient,
    event: ConversationEvent,
) -> None:
    """Publish a conversation event on the per-trace channel."""
    trace_id = event.trace_id
    if not trace_id:
        return
    try:
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to serialize trace conversation event: %s", exc)
        return
    try:
        await redis.publish(conversation_trace_channel(trace_id), payload)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to publish trace conversation event: %s", exc)


async def conversation_sse_stream(
    redis: redis_client.RedisClient,
    call_id: str,
) -> AsyncGenerator[str, None]:
    """Yield SSE data for a call-level channel."""
    channel = conversation_channel(call_id)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    heartbeat_interval = 15.0
    last_heartbeat = time.time()
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            now = time.time()
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
        finally:
            await pubsub.close()


async def conversation_sse_stream_by_trace(
    redis: redis_client.RedisClient,
    trace_id: str,
) -> AsyncGenerator[str, None]:
    """Yield SSE data for a trace-level channel."""
    channel = conversation_trace_channel(trace_id)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    heartbeat_interval = 15.0
    last_heartbeat = time.time()
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            now = time.time()
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
        finally:
            await pubsub.close()


__all__ = [
    "publish_conversation_event",
    "publish_trace_conversation_event",
    "conversation_sse_stream",
    "conversation_sse_stream_by_trace",
    "conversation_channel",
    "conversation_trace_channel",
]
