"""Unit tests for LuthienIndicatorPolicy."""

from __future__ import annotations

import asyncio
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
from litellm.types.utils import ModelResponse

from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import AnthropicResponse
from luthien_proxy.policies.luthien_indicator_policy import (
    INDICATOR_SUFFIX,
    LuthienIndicatorPolicy,
)
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyIOProtocol,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.streaming.stream_state import StreamState


class TestLuthienIndicatorPolicyInit:
    """Tests for policy initialization and config."""

    def test_inherits_correct_classes(self):
        policy = LuthienIndicatorPolicy()
        assert isinstance(policy, BasePolicy)
        assert isinstance(policy, OpenAIPolicyInterface)
        assert isinstance(policy, AnthropicExecutionInterface)

    def test_short_policy_name(self):
        policy = LuthienIndicatorPolicy()
        assert policy.short_policy_name == "LuthienIndicator"

    def test_default_indicator(self):
        policy = LuthienIndicatorPolicy()
        assert policy._indicator == INDICATOR_SUFFIX

    def test_custom_indicator(self):
        policy = LuthienIndicatorPolicy(config={"indicator": "\n[Custom]"})
        assert policy._indicator == "\n[Custom]"


class TestOpenAINonStreaming:
    """Tests for OpenAI non-streaming response modification."""

    @pytest.mark.asyncio
    async def test_appends_indicator_to_text(self, make_model_response):
        policy = LuthienIndicatorPolicy()
        ctx = PolicyContext.for_testing()
        response = make_model_response(content="Hello!")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "Hello!" + INDICATOR_SUFFIX

    @pytest.mark.asyncio
    async def test_empty_choices_unchanged(self):
        policy = LuthienIndicatorPolicy()
        ctx = PolicyContext.for_testing()
        response = ModelResponse(id="test", created=0, model="gpt-4", object="chat.completion", choices=[])

        result = await policy.on_openai_response(response, ctx)

        assert len(result.choices) == 0

    @pytest.mark.asyncio
    async def test_request_passes_through(self):
        policy = LuthienIndicatorPolicy()
        ctx = PolicyContext.for_testing()
        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

        result = await policy.on_openai_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_custom_indicator_appended(self, make_model_response):
        policy = LuthienIndicatorPolicy(config={"indicator": " [LOGGED]"})
        ctx = PolicyContext.for_testing()
        response = make_model_response(content="Hello!")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "Hello! [LOGGED]"


class TestAnthropicNonStreaming:
    """Tests for Anthropic non-streaming response modification."""

    def test_appends_text_block(self):
        policy = LuthienIndicatorPolicy()
        response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = policy._append_indicator_to_anthropic(response)

        assert len(result["content"]) == 2
        assert result["content"][0]["text"] == "Hello!"
        assert result["content"][1]["type"] == "text"
        assert result["content"][1]["text"] == INDICATOR_SUFFIX

    def test_preserves_tool_use_blocks(self):
        policy = LuthienIndicatorPolicy()
        tool_block = {"type": "tool_use", "id": "tool_1", "name": "search", "input": {"q": "test"}}
        response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_block],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = policy._append_indicator_to_anthropic(response)

        assert len(result["content"]) == 2
        assert result["content"][0] == tool_block
        assert result["content"][1]["type"] == "text"

    def test_empty_content_gets_indicator(self):
        policy = LuthienIndicatorPolicy()
        response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }

        result = policy._append_indicator_to_anthropic(response)

        assert len(result["content"]) == 1
        assert result["content"][0]["text"] == INDICATOR_SUFFIX


# -- Helpers for streaming tests -----------------------------------------------


class _StubAnthropicIO(AnthropicPolicyIOProtocol):
    """Minimal IO stub for testing run_anthropic."""

    def __init__(self, request: dict, stream_events: list[MessageStreamEvent] | None = None):
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


def _make_text_stream_events(text: str, index: int = 0) -> list[MessageStreamEvent]:
    """Build a minimal content_block_start/delta/stop sequence for a text block."""
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
            RawContentBlockStopEvent(type="content_block_stop", index=index),
        ),
    ]


class TestOpenAIStreaming:
    """Tests for OpenAI streaming indicator injection."""

    @pytest.mark.asyncio
    async def test_on_stream_complete_pushes_indicator_chunk(self):
        policy = LuthienIndicatorPolicy()
        stream_state = StreamState()
        policy_ctx = PolicyContext(
            transaction_id="test-txn",
            request=Request(model="test-model", messages=[{"role": "user", "content": "hi"}]),
        )
        egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()

        from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

        ctx = StreamingPolicyContext(
            policy_ctx=policy_ctx,
            egress_queue=egress_queue,
            original_streaming_response_state=stream_state,
            keepalive=lambda: None,
        )

        await policy.on_stream_complete(ctx)

        assert not egress_queue.empty()
        chunk = egress_queue.get_nowait()
        assert chunk.choices[0].delta.content == INDICATOR_SUFFIX

    @pytest.mark.asyncio
    async def test_on_chunk_received_forwards_chunk(self):
        policy = LuthienIndicatorPolicy()
        stream_state = StreamState()
        policy_ctx = PolicyContext(
            transaction_id="test-txn",
            request=Request(model="test-model", messages=[{"role": "user", "content": "hi"}]),
        )
        egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()

        from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

        ctx = StreamingPolicyContext(
            policy_ctx=policy_ctx,
            egress_queue=egress_queue,
            original_streaming_response_state=stream_state,
            keepalive=lambda: None,
        )

        from luthien_proxy.policy_core.chunk_builders import create_text_chunk

        original_chunk = create_text_chunk("Hello", model="test-model")
        stream_state.raw_chunks.append(original_chunk)

        await policy.on_chunk_received(ctx)

        assert not egress_queue.empty()
        forwarded = egress_queue.get_nowait()
        assert forwarded is original_chunk


class TestAnthropicStreaming:
    """Tests for Anthropic streaming indicator injection."""

    @pytest.mark.asyncio
    async def test_streaming_appends_indicator_events(self):
        """After the upstream stream ends, indicator content block events are emitted."""
        policy = LuthienIndicatorPolicy()
        ctx = PolicyContext.for_testing()

        upstream_events = _make_text_stream_events("Hello!", index=0)
        io = _StubAnthropicIO(
            request={"model": "claude-haiku-4-5-20251001", "messages": [], "max_tokens": 100, "stream": True},
            stream_events=upstream_events,
        )

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        # 3 upstream events + 3 indicator events
        assert len(emissions) == 6

        # Indicator events use index 1 (max_index 0 + 1)
        indicator_start = emissions[3]
        assert isinstance(indicator_start, RawContentBlockStartEvent)
        assert indicator_start.index == 1
        assert isinstance(indicator_start.content_block, TextBlock)

        indicator_delta = emissions[4]
        assert isinstance(indicator_delta, RawContentBlockDeltaEvent)
        assert indicator_delta.index == 1
        assert isinstance(indicator_delta.delta, TextDelta)
        assert indicator_delta.delta.text == INDICATOR_SUFFIX

        indicator_stop = emissions[5]
        assert isinstance(indicator_stop, RawContentBlockStopEvent)
        assert indicator_stop.index == 1

    @pytest.mark.asyncio
    async def test_streaming_with_multiple_blocks(self):
        """Indicator index follows the highest upstream block index."""
        policy = LuthienIndicatorPolicy()
        ctx = PolicyContext.for_testing()

        upstream_events = _make_text_stream_events("Hello", index=0) + _make_text_stream_events("World", index=1)
        io = _StubAnthropicIO(
            request={"model": "claude-haiku-4-5-20251001", "messages": [], "max_tokens": 100, "stream": True},
            stream_events=upstream_events,
        )

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        # 6 upstream + 3 indicator
        assert len(emissions) == 9
        indicator_start = emissions[6]
        assert isinstance(indicator_start, RawContentBlockStartEvent)
        assert indicator_start.index == 2

    @pytest.mark.asyncio
    async def test_streaming_custom_indicator(self):
        """Custom indicator text appears in the delta event."""
        policy = LuthienIndicatorPolicy(config={"indicator": " [LOGGED]"})
        ctx = PolicyContext.for_testing()

        upstream_events = _make_text_stream_events("Hi", index=0)
        io = _StubAnthropicIO(
            request={"model": "claude-haiku-4-5-20251001", "messages": [], "max_tokens": 100, "stream": True},
            stream_events=upstream_events,
        )

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        indicator_delta = emissions[4]
        assert isinstance(indicator_delta, RawContentBlockDeltaEvent)
        assert indicator_delta.delta.text == " [LOGGED]"

    @pytest.mark.asyncio
    async def test_non_streaming_still_works(self):
        """Non-streaming Anthropic path appends indicator as a content block."""
        policy = LuthienIndicatorPolicy()
        ctx = PolicyContext.for_testing()

        io = _StubAnthropicIO(
            request={"model": "claude-haiku-4-5-20251001", "messages": [], "max_tokens": 100, "stream": False},
        )

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        assert len(emissions) == 1
        response = emissions[0]
        assert isinstance(response, dict)
        assert len(response["content"]) == 2
        assert response["content"][1]["text"] == INDICATOR_SUFFIX


__all__ = []
