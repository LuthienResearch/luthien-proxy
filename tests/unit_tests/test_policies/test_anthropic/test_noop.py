# ABOUTME: Tests for AnthropicNoOpPolicy verifying passthrough behavior
"""Tests for AnthropicNoOpPolicy.

Verifies that AnthropicNoOpPolicy:
1. Implements the AnthropicPolicyProtocol
2. Passes through requests unchanged
3. Passes through responses unchanged
4. Passes through stream events unchanged
"""

import pytest

from luthien_proxy.llm.types.anthropic import (
    AnthropicContentBlockDeltaEvent,
    AnthropicContentBlockStartEvent,
    AnthropicContentBlockStopEvent,
    AnthropicMessageDeltaEvent,
    AnthropicMessageStartEvent,
    AnthropicMessageStopEvent,
    AnthropicPingEvent,
    AnthropicRequest,
    AnthropicResponse,
    AnthropicStreamingEvent,
    AnthropicTextBlock,
    AnthropicTextDelta,
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

        request: AnthropicRequest = {
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

        request: AnthropicRequest = {
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

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello!"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_on_response_preserves_content(self):
        """on_response preserves content blocks exactly."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Complex response text"}
        response: AnthropicResponse = {
            "id": "msg_456",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }

        result = await policy.on_response(response, ctx)

        assert result["content"][0]["text"] == "Complex response text"
        assert result["usage"]["input_tokens"] == 20
        assert result["usage"]["output_tokens"] == 10


class TestAnthropicNoOpPolicyStreamEvent:
    """Tests for on_stream_event passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_stream_event_returns_same_event(self):
        """on_stream_event returns the exact same event object."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        text_delta: AnthropicTextDelta = {"type": "text_delta", "text": "Hello"}
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": text_delta,
        }

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_never_returns_none(self):
        """on_stream_event never filters out events (returns None)."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        events: list[AnthropicStreamingEvent] = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-sonnet-4-20250514",
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hi"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
            {"type": "ping"},
        ]

        for event in events:
            result = await policy.on_stream_event(event, ctx)
            assert result is not None, f"Event of type {event['type']} was filtered out"
            assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_all_event_types(self):
        """on_stream_event handles all Anthropic stream event types."""
        policy = AnthropicNoOpPolicy()
        ctx = PolicyContext.for_testing()

        message_start: AnthropicMessageStartEvent = {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-sonnet-4-20250514",
                "stop_reason": None,
                "usage": {"input_tokens": 5, "output_tokens": 0},
            },
        }

        content_block_start: AnthropicContentBlockStartEvent = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }

        content_block_delta: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "test"},
        }

        content_block_stop: AnthropicContentBlockStopEvent = {
            "type": "content_block_stop",
            "index": 0,
        }

        message_delta: AnthropicMessageDeltaEvent = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 1},
        }

        message_stop: AnthropicMessageStopEvent = {"type": "message_stop"}

        ping: AnthropicPingEvent = {"type": "ping"}

        assert await policy.on_stream_event(message_start, ctx) is message_start
        assert await policy.on_stream_event(content_block_start, ctx) is content_block_start
        assert await policy.on_stream_event(content_block_delta, ctx) is content_block_delta
        assert await policy.on_stream_event(content_block_stop, ctx) is content_block_stop
        assert await policy.on_stream_event(message_delta, ctx) is message_delta
        assert await policy.on_stream_event(message_stop, ctx) is message_stop
        assert await policy.on_stream_event(ping, ctx) is ping
