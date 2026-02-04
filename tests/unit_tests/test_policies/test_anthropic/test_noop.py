# ABOUTME: Tests for AnthropicNoOpPolicy verifying passthrough behavior
"""Tests for AnthropicNoOpPolicy.

Verifies that AnthropicNoOpPolicy:
1. Implements the AnthropicPolicyProtocol
2. Passes through requests unchanged
3. Passes through responses unchanged
4. Passes through stream events unchanged
"""

import pytest
from anthropic.types import (
    Message,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
)

from luthien_proxy.policies.anthropic.noop import AnthropicNoOpPolicy
from luthien_proxy.policy_core.anthropic_protocol import AnthropicPolicyProtocol
from luthien_proxy.policy_core.policy_context import PolicyContext


class TestAnthropicNoOpPolicyProtocol:
    """Tests verifying AnthropicNoOpPolicy implements the protocol."""

    def test_implements_protocol(self):
        """AnthropicNoOpPolicy satisfies AnthropicPolicyProtocol."""
        policy = AnthropicNoOpPolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)

    def test_has_short_policy_name(self):
        """AnthropicNoOpPolicy has a short_policy_name property."""
        policy = AnthropicNoOpPolicy()
        assert policy.short_policy_name == "AnthropicNoOp"


class TestAnthropicNoOpPolicyRequest:
    """Tests for on_request passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_request_returns_same_request(self):
        """on_request returns the exact same request object."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        request = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_request_preserves_all_fields(self):
        """on_request preserves all fields in a complex request."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        request = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "How are you?"},
            ],
            "max_tokens": 500,
            "temperature": 0.7,
            "system": "You are a helpful assistant.",
        }

        result = await policy.on_request(request, ctx)

        assert result["model"] == "claude-sonnet-4-20250514"
        assert len(result["messages"]) == 3
        assert result["max_tokens"] == 500
        assert result.get("temperature") == 0.7
        assert result.get("system") == "You are a helpful assistant."


class TestAnthropicNoOpPolicyResponse:
    """Tests for on_response passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_response_returns_same_response(self):
        """on_response returns the exact same response object."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        response = Message.model_construct(
            id="msg_123",
            type="message",
            role="assistant",
            content=[TextBlock.model_construct(type="text", text="Hello!")],
            model="claude-sonnet-4-20250514",
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 5},
        )

        result = await policy.on_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_on_response_preserves_content(self):
        """on_response preserves content blocks exactly."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        response = Message.model_construct(
            id="msg_456",
            type="message",
            role="assistant",
            content=[TextBlock.model_construct(type="text", text="Complex response text")],
            model="claude-sonnet-4-20250514",
            stop_reason="end_turn",
            usage={"input_tokens": 20, "output_tokens": 10},
        )

        result = await policy.on_response(response, ctx)

        assert result.content[0].text == "Complex response text"
        assert result.usage.input_tokens == 20
        assert result.usage.output_tokens == 10


class TestAnthropicNoOpPolicyStreamEvent:
    """Tests for on_stream_event passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_stream_event_returns_same_event(self):
        """on_stream_event returns the exact same event object."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="Hello"),
        )

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_never_returns_none(self):
        """on_stream_event never filters out events (returns None)."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        events = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-sonnet-4-20250514",
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            ),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=0,
                content_block=TextBlock.model_construct(type="text", text=""),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="Hi"),
            ),
            RawContentBlockStopEvent.model_construct(
                type="content_block_stop",
                index=0,
            ),
            RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta={"stop_reason": "end_turn", "stop_sequence": None},
                usage={"output_tokens": 1},
            ),
            RawMessageStopEvent.model_construct(type="message_stop"),
        ]

        for event in events:
            result = await policy.on_stream_event(event, ctx)
            assert result is not None, f"Event of type {event.type} was filtered out"
            assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_all_event_types(self):
        """on_stream_event handles all Anthropic stream event types."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        message_start = RawMessageStartEvent.model_construct(
            type="message_start",
            message={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-sonnet-4-20250514",
                "stop_reason": None,
                "usage": {"input_tokens": 5, "output_tokens": 0},
            },
        )

        content_block_start = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )

        content_block_delta = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="test"),
        )

        content_block_stop = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        message_delta = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 1},
        )

        message_stop = RawMessageStopEvent.model_construct(type="message_stop")

        assert await policy.on_stream_event(message_start, ctx) is message_start
        assert await policy.on_stream_event(content_block_start, ctx) is content_block_start
        assert await policy.on_stream_event(content_block_delta, ctx) is content_block_delta
        assert await policy.on_stream_event(content_block_stop, ctx) is content_block_stop
        assert await policy.on_stream_event(message_delta, ctx) is message_delta
        assert await policy.on_stream_event(message_stop, ctx) is message_stop
