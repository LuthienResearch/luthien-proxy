import asyncio
from collections import deque

import pytest

from luthien_proxy.control_plane.conversation.streams import (
    ConversationStreamConfig,
    conversation_sse_stream,
)


class _FakePubSub:
    def __init__(self, messages: deque[dict]):
        self._messages = messages
        self.unsubscribed = False
        self.closed = False
        self.channel = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def subscribe(self, channel: str) -> None:
        self.channel = channel

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed = True

    async def close(self) -> None:
        self.closed = True

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float):
        await asyncio.sleep(0)
        if self._messages:
            return self._messages.popleft()
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return None
        return None


class _FakeRedis:
    def __init__(self, pubsub: _FakePubSub):
        self._pubsub = pubsub
        self.pubsub_call_kwargs = None

    def pubsub(self, **kwargs):
        self.pubsub_call_kwargs = kwargs
        return self._pubsub


@pytest.mark.asyncio
async def test_conversation_stream_yields_events_and_heartbeat():
    messages = deque([{"data": b'{"hello":"world"}'}])
    fake_pubsub = _FakePubSub(messages)
    redis = _FakeRedis(fake_pubsub)
    config = ConversationStreamConfig(
        heartbeat_seconds=0.01,
        redis_poll_timeout_seconds=0.01,
        rate_limit_max_requests=10,
        rate_limit_window_seconds=1.0,
    )

    stream = conversation_sse_stream(redis, "call-123", config=config)

    first = await asyncio.wait_for(anext(stream), timeout=0.5)
    assert first == 'data: {"hello":"world"}\n\n'

    await asyncio.sleep(0.02)
    second = await asyncio.wait_for(anext(stream), timeout=0.5)
    assert second == ": ping\n\n"

    await stream.aclose()
    await asyncio.sleep(0)
    assert redis.pubsub_call_kwargs == {}
