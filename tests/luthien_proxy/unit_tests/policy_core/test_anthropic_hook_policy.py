"""Tests for AnthropicHookPolicy default hook-based implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextBlock,
    TextDelta,
)

from luthien_proxy.llm.types.anthropic import AnthropicResponse
from luthien_proxy.policy_core.anthropic_hook_policy import AnthropicHookPolicy
from luthien_proxy.policy_core.policy_context import PolicyContext

# -- Stub IO for testing -------------------------------------------------------


class _StubIO:
    """Minimal IO stub for testing run_anthropic."""

    def __init__(self, request: dict, stream_events: list | None = None):
        self._request = request
        self._stream_events = stream_events or []
        self._first_backend_response: AnthropicResponse | None = None

    @property
    def request(self) -> dict:
        return self._request

    def set_request(self, request: dict) -> None:
        self._request = request

    @property
    def first_backend_response(self) -> AnthropicResponse | None:
        return self._first_backend_response

    async def complete(self, request: dict | None = None) -> AnthropicResponse:
        response: AnthropicResponse = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        self._first_backend_response = response
        return response

    def stream(self, request: dict | None = None) -> AsyncIterator[MessageStreamEvent]:
        events = self._stream_events

        async def _gen() -> AsyncIterator[MessageStreamEvent]:
            for event in events:
                yield event

        return _gen()


def _text_events(text: str, index: int = 0) -> list:
    """Build content_block_start/delta/stop for a text block."""
    from typing import cast

    return [
        cast(
            MessageStreamEvent,
            RawContentBlockStartEvent(
                type="content_block_start",
                index=index,
                content_block=TextBlock(type="text", text=""),
            ),
        ),
        cast(
            MessageStreamEvent,
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=index,
                delta=TextDelta(type="text_delta", text=text),
            ),
        ),
        cast(
            MessageStreamEvent,
            RawContentBlockStopEvent(
                type="content_block_stop",
                index=index,
            ),
        ),
    ]


# -- Test base class with no overrides -----------------------------------------


class TestAnthropicHookPolicyDefaults:
    """Test that the base class works as a passthrough with no overrides."""

    @pytest.mark.asyncio
    async def test_non_streaming_passthrough(self):
        policy = AnthropicHookPolicy()
        ctx = PolicyContext.for_testing()
        io = _StubIO(request={"model": "test", "messages": [], "max_tokens": 10, "stream": False})

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        assert len(emissions) == 1
        assert isinstance(emissions[0], dict)
        assert emissions[0]["content"][0]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_streaming_passthrough(self):
        policy = AnthropicHookPolicy()
        ctx = PolicyContext.for_testing()
        events = _text_events("world")
        io = _StubIO(
            request={"model": "test", "messages": [], "max_tokens": 10, "stream": True},
            stream_events=events,
        )

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        # 3 upstream events, no extras from stream_complete
        assert len(emissions) == 3

    @pytest.mark.asyncio
    async def test_request_hook_called(self):
        """on_anthropic_request can modify the request."""

        class ModifyRequest(AnthropicHookPolicy):
            async def on_anthropic_request(self, request, context):
                request["model"] = "modified"
                return request

        policy = ModifyRequest()
        ctx = PolicyContext.for_testing()
        io = _StubIO(request={"model": "original", "messages": [], "max_tokens": 10, "stream": False})

        [e async for e in policy.run_anthropic(io, ctx)]

        assert io.request["model"] == "modified"

    @pytest.mark.asyncio
    async def test_response_hook_called(self):
        """on_anthropic_response can modify the response."""

        class ModifyResponse(AnthropicHookPolicy):
            async def on_anthropic_response(self, response, context):
                response["content"][0]["text"] = "MODIFIED"
                return response

        policy = ModifyResponse()
        ctx = PolicyContext.for_testing()
        io = _StubIO(request={"model": "test", "messages": [], "max_tokens": 10, "stream": False})

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        assert emissions[0]["content"][0]["text"] == "MODIFIED"

    @pytest.mark.asyncio
    async def test_stream_event_hook_called(self):
        """on_anthropic_stream_event can filter/transform events."""

        class FilterEvents(AnthropicHookPolicy):
            async def on_anthropic_stream_event(self, event, context):
                if isinstance(event, RawContentBlockStartEvent):
                    return [event]
                return []

        policy = FilterEvents()
        ctx = PolicyContext.for_testing()
        events = _text_events("hi")
        io = _StubIO(
            request={"model": "test", "messages": [], "max_tokens": 10, "stream": True},
            stream_events=events,
        )

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        assert len(emissions) == 1
        assert isinstance(emissions[0], RawContentBlockStartEvent)

    @pytest.mark.asyncio
    async def test_stream_complete_hook_emits_extra_events(self):
        """on_anthropic_stream_complete can emit events after the stream."""
        from typing import cast

        class AppendAfterStream(AnthropicHookPolicy):
            async def on_anthropic_stream_complete(self, context):
                return [
                    cast(
                        MessageStreamEvent,
                        RawContentBlockStartEvent(
                            type="content_block_start",
                            index=99,
                            content_block=TextBlock(type="text", text=""),
                        ),
                    ),
                ]

        policy = AppendAfterStream()
        ctx = PolicyContext.for_testing()
        events = _text_events("hi")
        io = _StubIO(
            request={"model": "test", "messages": [], "max_tokens": 10, "stream": True},
            stream_events=events,
        )

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        # 3 upstream + 1 from stream_complete
        assert len(emissions) == 4
        assert isinstance(emissions[3], RawContentBlockStartEvent)
        assert emissions[3].index == 99

    @pytest.mark.asyncio
    async def test_stream_complete_default_returns_empty(self):
        """Default on_anthropic_stream_complete returns empty list."""
        policy = AnthropicHookPolicy()
        ctx = PolicyContext.for_testing()
        result = await policy.on_anthropic_stream_complete(ctx)
        assert result == []
