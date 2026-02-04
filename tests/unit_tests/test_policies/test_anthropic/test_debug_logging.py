# ABOUTME: Tests for AnthropicDebugLoggingPolicy verifying logging and passthrough behavior
"""Tests for AnthropicDebugLoggingPolicy.

Verifies that AnthropicDebugLoggingPolicy:
1. Implements the AnthropicPolicyProtocol
2. Logs request data and passes through unchanged
3. Logs response data and passes through unchanged
4. Logs stream events and passes through unchanged
5. Records events to context for DB persistence
"""

from unittest.mock import Mock

import pytest
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
)

from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
)
from luthien_proxy.policies.anthropic.debug_logging import AnthropicDebugLoggingPolicy
from luthien_proxy.policy_core.anthropic_protocol import AnthropicPolicyProtocol
from luthien_proxy.policy_core.policy_context import PolicyContext


def create_mock_policy_context() -> Mock:
    """Create a mock PolicyContext for testing event recording."""
    ctx = Mock(spec=PolicyContext)
    ctx.transaction_id = "test-transaction-id"
    ctx.scratchpad = {}
    ctx.record_event = Mock()
    return ctx


class TestAnthropicDebugLoggingPolicyProtocol:
    """Tests verifying AnthropicDebugLoggingPolicy implements the protocol."""

    def test_implements_protocol(self):
        """AnthropicDebugLoggingPolicy satisfies AnthropicPolicyProtocol."""
        policy = AnthropicDebugLoggingPolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)

    def test_has_short_policy_name(self):
        """AnthropicDebugLoggingPolicy has correct short_policy_name property."""
        policy = AnthropicDebugLoggingPolicy()
        assert policy.short_policy_name == "AnthropicDebugLogging"


class TestAnthropicDebugLoggingPolicyRequest:
    """Tests for on_request logging and passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_request_returns_same_request(self):
        """on_request returns the exact same request object unchanged."""
        policy = AnthropicDebugLoggingPolicy()
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
        policy = AnthropicDebugLoggingPolicy()
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

    @pytest.mark.asyncio
    async def test_on_request_records_event(self):
        """on_request records debug event to context."""
        policy = AnthropicDebugLoggingPolicy()
        ctx = create_mock_policy_context()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "system": "You are helpful.",
        }

        await policy.on_request(request, ctx)

        # Check that an event was recorded
        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        assert call_args[0][0] == "debug.anthropic_request"
        assert call_args[0][1]["model"] == "claude-sonnet-4-20250514"
        assert call_args[0][1]["message_count"] == 1
        assert call_args[0][1]["max_tokens"] == 100
        assert call_args[0][1]["has_system"] is True


class TestAnthropicDebugLoggingPolicyResponse:
    """Tests for on_response logging and passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_response_returns_same_response(self):
        """on_response returns the exact same response object unchanged."""
        policy = AnthropicDebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_on_response_preserves_content(self):
        """on_response preserves content blocks exactly."""
        policy = AnthropicDebugLoggingPolicy()
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

    @pytest.mark.asyncio
    async def test_on_response_records_event(self):
        """on_response records debug event to context."""
        policy = AnthropicDebugLoggingPolicy()
        ctx = create_mock_policy_context()

        response: AnthropicResponse = {
            "id": "msg_789",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        await policy.on_response(response, ctx)

        # Check that an event was recorded
        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        assert call_args[0][0] == "debug.anthropic_response"
        assert call_args[0][1]["id"] == "msg_789"
        assert call_args[0][1]["model"] == "claude-sonnet-4-20250514"
        assert call_args[0][1]["stop_reason"] == "end_turn"
        assert call_args[0][1]["content_block_count"] == 1


class TestAnthropicDebugLoggingPolicyStreamEvent:
    """Tests for on_stream_event logging and passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_stream_event_returns_same_event(self):
        """on_stream_event returns the exact same event object unchanged."""
        policy = AnthropicDebugLoggingPolicy()
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
        policy = AnthropicDebugLoggingPolicy()
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
    async def test_on_stream_event_records_event(self):
        """on_stream_event records debug event to context."""
        policy = AnthropicDebugLoggingPolicy()
        ctx = create_mock_policy_context()

        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="Hello"),
        )

        await policy.on_stream_event(event, ctx)

        # Check that an event was recorded
        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        assert call_args[0][0] == "debug.anthropic_stream_event"
        assert call_args[0][1]["event_type"] == "content_block_delta"

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_all_event_types(self):
        """on_stream_event handles all Anthropic stream event types."""
        policy = AnthropicDebugLoggingPolicy()
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


class TestAnthropicDebugLoggingPolicyLogging:
    """Tests for logging output."""

    @pytest.mark.asyncio
    async def test_on_request_logs_at_info_level(self, caplog):
        """on_request logs request data at INFO level."""
        policy = AnthropicDebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        with caplog.at_level("INFO"):
            await policy.on_request(request, ctx)

        assert "[ANTHROPIC_REQUEST]" in caplog.text
        assert "claude-sonnet-4-20250514" in caplog.text

    @pytest.mark.asyncio
    async def test_on_response_logs_at_info_level(self, caplog):
        """on_response logs response data at INFO level."""
        policy = AnthropicDebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with caplog.at_level("INFO"):
            await policy.on_response(response, ctx)

        assert "[ANTHROPIC_RESPONSE]" in caplog.text
        assert "msg_123" in caplog.text

    @pytest.mark.asyncio
    async def test_on_stream_event_logs_at_info_level(self, caplog):
        """on_stream_event logs event data at INFO level."""
        policy = AnthropicDebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="Hello"),
        )

        with caplog.at_level("INFO"):
            await policy.on_stream_event(event, ctx)

        assert "[ANTHROPIC_STREAM_EVENT]" in caplog.text
        assert "content_block_delta" in caplog.text


__all__ = [
    "TestAnthropicDebugLoggingPolicyProtocol",
    "TestAnthropicDebugLoggingPolicyRequest",
    "TestAnthropicDebugLoggingPolicyResponse",
    "TestAnthropicDebugLoggingPolicyStreamEvent",
    "TestAnthropicDebugLoggingPolicyLogging",
]
