"""Streaming helpers for conversation events."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from contextlib import suppress
from typing import AsyncGenerator

from luthien_proxy.utils import redis_client

from .models import ConversationEvent

logger = logging.getLogger(__name__)

_CONVERSATION_CHANNEL_PREFIX = "luthien:conversation:"
_CONVERSATION_TRACE_CHANNEL_PREFIX = "luthien:conversation-trace:"
DEFAULT_HEARTBEAT_INTERVAL = 15.0
STREAM_POLL_TIMEOUT = 1.0


async def _close_resource(resource: object) -> None:
    """Best-effort asynchronous close for redis resources."""
    if resource is None:
        return
    close_async = getattr(resource, "aclose", None)
    if callable(close_async):
        result = close_async()
        if inspect.isawaitable(result):
            with suppress(Exception):
                await result
        return
    close = getattr(resource, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            with suppress(Exception):
                await result


async def _cleanup_pubsub(client: object, pubsub: object, channel: str) -> None:
    """Ensure pubsub connections are released back to the pool."""
    if pubsub is not None:
        with suppress(Exception):
            await pubsub.unsubscribe(channel)
    await _close_resource(pubsub)
    await _close_resource(client)


async def _stream_channel(
    redis: redis_client.RedisClient,
    channel: str,
    heartbeat_interval: float | None,
) -> AsyncGenerator[str, None]:
    client = redis.client()
    pubsub = None
    try:
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
    except Exception:
        await _cleanup_pubsub(client, pubsub, channel)
        raise
    interval = DEFAULT_HEARTBEAT_INTERVAL if not heartbeat_interval or heartbeat_interval <= 0 else heartbeat_interval
    last_heartbeat = time.time()
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=STREAM_POLL_TIMEOUT)
            now = time.time()
            if message is None:
                if now - last_heartbeat >= interval:
                    last_heartbeat = now
                    yield ": ping\n\n"
                continue
            data = message.get("data")
            text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)
            last_heartbeat = now
            yield f"data: {text}\n\n"
    except asyncio.CancelledError:
        raise
    finally:
        await _cleanup_pubsub(client, pubsub, channel)


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
    heartbeat_interval: float | None = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE data for a call-level channel."""
    channel = conversation_channel(call_id)
    async for payload in _stream_channel(redis, channel, heartbeat_interval):
        yield payload


async def conversation_sse_stream_by_trace(
    redis: redis_client.RedisClient,
    trace_id: str,
    heartbeat_interval: float | None = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE data for a trace-level channel."""
    channel = conversation_trace_channel(trace_id)
    async for payload in _stream_channel(redis, channel, heartbeat_interval):
        yield payload


__all__ = [
    "publish_conversation_event",
    "publish_trace_conversation_event",
    "conversation_sse_stream",
    "conversation_sse_stream_by_trace",
    "conversation_channel",
    "conversation_trace_channel",
]
