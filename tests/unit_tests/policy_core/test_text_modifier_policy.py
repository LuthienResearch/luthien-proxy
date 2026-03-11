"""Tests for TextModifierPolicy base class."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

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

from luthien_proxy.llm.types.anthropic import AnthropicResponse
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.policy_core.text_modifier_policy import TextModifierPolicy
from luthien_proxy.streaming.stream_state import StreamState

# -- Concrete subclasses for testing -------------------------------------------


class UpperCasePolicy(TextModifierPolicy):
    """Test policy: uppercases all text."""

    def modify_text(self, text: str) -> str:
        return text.upper()


class AppendSuffixPolicy(TextModifierPolicy):
    """Test policy: appends a suffix after all content."""

    def extra_text(self) -> str | None:
        return "\n--SUFFIX"


class BothPolicy(TextModifierPolicy):
    """Test policy: uppercases AND appends."""

    def modify_text(self, text: str) -> str:
        return text.upper()

    def extra_text(self) -> str | None:
        return "\n--END"


# -- Helpers -------------------------------------------------------------------


def _text_events(text: str, index: int = 0) -> list[MessageStreamEvent]:
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


class _StubIO:
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
            "model": "test",
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


def _make_streaming_ctx() -> StreamingPolicyContext:
    stream_state = StreamState()
    policy_ctx = PolicyContext.for_testing()
    egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()
    return StreamingPolicyContext(
        policy_ctx=policy_ctx,
        egress_queue=egress_queue,
        original_streaming_response_state=stream_state,
        keepalive=lambda: None,
    )


# -- Tests: modify_text -------------------------------------------------------


class TestModifyTextOpenAINonStreaming:
    @pytest.mark.asyncio
    async def test_uppercases_response(self, make_model_response):
        policy = UpperCasePolicy()
        ctx = PolicyContext.for_testing()
        response = make_model_response(content="hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "HELLO WORLD"


class TestModifyTextOpenAIStreaming:
    @pytest.mark.asyncio
    async def test_uppercases_chunk(self):
        from litellm.types.utils import StreamingChoices

        from luthien_proxy.policy_core.chunk_builders import create_text_chunk

        policy = UpperCasePolicy()
        ctx = _make_streaming_ctx()
        chunk = create_text_chunk("hello", model="test")
        ctx.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_chunk_received(ctx)

        forwarded = ctx.egress_queue.get_nowait()
        choice = cast(StreamingChoices, forwarded.choices[0])
        assert choice.delta.content == "HELLO"


class TestModifyTextAnthropicNonStreaming:
    @pytest.mark.asyncio
    async def test_uppercases_text_blocks(self):
        policy = UpperCasePolicy()
        ctx = PolicyContext.for_testing()
        io = _StubIO(request={"model": "test", "messages": [], "max_tokens": 10, "stream": False})

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        assert emissions[0]["content"][0]["text"] == "HELLO"


class TestModifyTextAnthropicStreaming:
    @pytest.mark.asyncio
    async def test_uppercases_text_delta(self):
        policy = UpperCasePolicy()
        ctx = PolicyContext.for_testing()
        events = _text_events("hello")
        io = _StubIO(
            request={"model": "test", "messages": [], "max_tokens": 10, "stream": True},
            stream_events=events,
        )

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        deltas = [e for e in emissions if isinstance(e, RawContentBlockDeltaEvent)]
        assert len(deltas) == 1
        assert deltas[0].delta.text == "HELLO"


# -- Tests: extra_text ---------------------------------------------------------


class TestExtraTextOpenAINonStreaming:
    @pytest.mark.asyncio
    async def test_appends_suffix(self, make_model_response):
        policy = AppendSuffixPolicy()
        ctx = PolicyContext.for_testing()
        response = make_model_response(content="hello")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "hello\n--SUFFIX"


class TestExtraTextOpenAIStreaming:
    @pytest.mark.asyncio
    async def test_emits_suffix_chunk_on_stream_complete(self):
        policy = AppendSuffixPolicy()
        ctx = _make_streaming_ctx()
        ctx.original_streaming_response_state.raw_chunks.append(
            ModelResponse(id="test", created=0, model="test", object="chat.completion.chunk", choices=[])
        )

        await policy.on_stream_complete(ctx)

        chunk = ctx.egress_queue.get_nowait()
        assert chunk.choices[0].delta.content == "\n--SUFFIX"


class TestExtraTextAnthropicNonStreaming:
    @pytest.mark.asyncio
    async def test_appends_text_block(self):
        policy = AppendSuffixPolicy()
        ctx = PolicyContext.for_testing()
        io = _StubIO(request={"model": "test", "messages": [], "max_tokens": 10, "stream": False})

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        content = emissions[0]["content"]
        assert len(content) == 2
        assert content[0]["text"] == "hello"
        assert content[1]["text"] == "\n--SUFFIX"


class TestExtraTextAnthropicStreaming:
    @pytest.mark.asyncio
    async def test_emits_suffix_events_after_stream(self):
        policy = AppendSuffixPolicy()
        ctx = PolicyContext.for_testing()
        events = _text_events("hello")
        io = _StubIO(
            request={"model": "test", "messages": [], "max_tokens": 10, "stream": True},
            stream_events=events,
        )

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        # 3 upstream + 3 suffix events (start/delta/stop)
        assert len(emissions) == 6
        suffix_delta = emissions[4]
        assert isinstance(suffix_delta, RawContentBlockDeltaEvent)
        assert suffix_delta.delta.text == "\n--SUFFIX"
        assert suffix_delta.index == 1  # max upstream index (0) + 1


# -- Tests: both modify + extra ------------------------------------------------


class TestBothPolicyCombined:
    @pytest.mark.asyncio
    async def test_openai_non_streaming(self, make_model_response):
        policy = BothPolicy()
        ctx = PolicyContext.for_testing()
        response = make_model_response(content="hello")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "HELLO\n--END"

    @pytest.mark.asyncio
    async def test_anthropic_non_streaming(self):
        policy = BothPolicy()
        ctx = PolicyContext.for_testing()
        io = _StubIO(request={"model": "test", "messages": [], "max_tokens": 10, "stream": False})

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        content = emissions[0]["content"]
        assert content[0]["text"] == "HELLO"
        assert content[1]["text"] == "\n--END"


# -- Tests: no-op default (neither method overridden) --------------------------


class TestDefaultPassthrough:
    @pytest.mark.asyncio
    async def test_openai_passthrough(self, make_model_response):
        policy = TextModifierPolicy()
        ctx = PolicyContext.for_testing()
        response = make_model_response(content="unchanged")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "unchanged"

    @pytest.mark.asyncio
    async def test_anthropic_passthrough(self):
        policy = TextModifierPolicy()
        ctx = PolicyContext.for_testing()
        io = _StubIO(request={"model": "test", "messages": [], "max_tokens": 10, "stream": False})

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        assert emissions[0]["content"][0]["text"] == "hello"
        assert len(emissions[0]["content"]) == 1
