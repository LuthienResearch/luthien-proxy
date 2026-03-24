"""Unit tests for DebugLoggingPolicy.

Tests cover:
1. Policy name and interface inheritance
2. Anthropic interface: on_anthropic_request, on_anthropic_response, on_anthropic_stream_event
3. Event recording for DB persistence
"""

from __future__ import annotations

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

from tests.constants import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
)
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.debug_logging_policy import DebugLoggingPolicy
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    BasePolicy,
)


def create_mock_policy_context() -> PolicyContext:
    """Create a mock PolicyContext for testing."""
    ctx = Mock(spec=PolicyContext)
    ctx.transaction_id = "test-transaction-id"
    ctx.scratchpad = {}
    ctx.record_event = Mock()
    return ctx


# =============================================================================
# Interface and Property Tests
# =============================================================================


class TestDebugLoggingPolicyProperties:
    """Test DebugLoggingPolicy properties and interface inheritance."""

    def test_short_policy_name(self):
        """Test short_policy_name property."""
        policy = DebugLoggingPolicy()
        assert policy.short_policy_name == "DebugLogging"

    def test_inherits_from_base_policy(self):
        """Test that DebugLoggingPolicy inherits from BasePolicy."""
        policy = DebugLoggingPolicy()
        assert isinstance(policy, BasePolicy)

    def test_implements_anthropic_interface(self):
        """Test that DebugLoggingPolicy implements AnthropicExecutionInterface."""
        policy = DebugLoggingPolicy()
        assert isinstance(policy, AnthropicExecutionInterface)


# =============================================================================
# Anthropic Interface Tests
# =============================================================================


class TestDebugLoggingPolicyAnthropicRequest:
    """Tests for on_anthropic_request logging and passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_request_returns_same_request(self):
        """on_anthropic_request returns the exact same request object unchanged."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_anthropic_request_preserves_all_fields(self):
        """on_anthropic_request preserves all fields in a complex request."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "How are you?"},
            ],
            "max_tokens": 500,
            "temperature": 0.7,
            "system": "You are a helpful assistant.",
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result["model"] == DEFAULT_TEST_MODEL
        assert len(result["messages"]) == 3
        assert result["max_tokens"] == 500
        assert result.get("temperature") == 0.7
        assert result.get("system") == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_on_anthropic_request_records_event(self):
        """on_anthropic_request records debug event to context."""
        policy = DebugLoggingPolicy()
        ctx = create_mock_policy_context()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "system": "You are helpful.",
        }

        await policy.on_anthropic_request(request, ctx)

        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        assert call_args[0][0] == "debug.anthropic_request"
        assert call_args[0][1]["model"] == DEFAULT_TEST_MODEL
        assert call_args[0][1]["message_count"] == 1
        assert call_args[0][1]["max_tokens"] == 100
        assert call_args[0][1]["has_system"] is True


class TestDebugLoggingPolicyAnthropicResponse:
    """Tests for on_anthropic_response logging and passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_response_returns_same_response(self):
        """on_anthropic_response returns the exact same response object unchanged."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_on_anthropic_response_preserves_content(self):
        """on_anthropic_response preserves content blocks exactly."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Complex response text"}
        response: AnthropicResponse = {
            "id": "msg_456",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["text"] == "Complex response text"
        assert result["usage"]["input_tokens"] == 20
        assert result["usage"]["output_tokens"] == 10

    @pytest.mark.asyncio
    async def test_on_anthropic_response_records_event(self):
        """on_anthropic_response records debug event to context."""
        policy = DebugLoggingPolicy()
        ctx = create_mock_policy_context()

        response: AnthropicResponse = {
            "id": "msg_789",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        await policy.on_anthropic_response(response, ctx)

        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        assert call_args[0][0] == "debug.anthropic_response"
        assert call_args[0][1]["id"] == "msg_789"
        assert call_args[0][1]["model"] == DEFAULT_TEST_MODEL
        assert call_args[0][1]["stop_reason"] == "end_turn"
        assert call_args[0][1]["content_block_count"] == 1


class TestDebugLoggingPolicyAnthropicStreamEvent:
    """Tests for on_anthropic_stream_event logging and passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_returns_same_event(self):
        """on_anthropic_stream_event returns the exact same event object unchanged."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="Hello"),
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_never_returns_empty_list(self):
        """on_anthropic_stream_event never filters out events (returns empty list)."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        events = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": DEFAULT_TEST_MODEL,
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
            result = await policy.on_anthropic_stream_event(event, ctx)
            assert len(result) > 0, f"Event of type {event.type} was filtered out"
            assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_records_event(self):
        """on_anthropic_stream_event records debug event to context."""
        policy = DebugLoggingPolicy()
        ctx = create_mock_policy_context()

        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="Hello"),
        )

        await policy.on_anthropic_stream_event(event, ctx)

        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        assert call_args[0][0] == "debug.anthropic_stream_event"
        assert call_args[0][1]["event_type"] == "content_block_delta"

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_all_event_types(self):
        """on_anthropic_stream_event handles all Anthropic stream event types."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        message_start = RawMessageStartEvent.model_construct(
            type="message_start",
            message={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": DEFAULT_TEST_MODEL,
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

        assert await policy.on_anthropic_stream_event(message_start, ctx) == [message_start]
        assert await policy.on_anthropic_stream_event(content_block_start, ctx) == [content_block_start]
        assert await policy.on_anthropic_stream_event(content_block_delta, ctx) == [content_block_delta]
        assert await policy.on_anthropic_stream_event(content_block_stop, ctx) == [content_block_stop]
        assert await policy.on_anthropic_stream_event(message_delta, ctx) == [message_delta]
        assert await policy.on_anthropic_stream_event(message_stop, ctx) == [message_stop]


# =============================================================================
# Header Sanitization Tests
# =============================================================================


# =============================================================================
# Logging Output Tests
# =============================================================================


class TestDebugLoggingPolicyLogging:
    """Tests for logging output."""

    @pytest.mark.asyncio
    async def test_on_anthropic_request_logs_at_info_level(self, caplog):
        """on_anthropic_request logs request data at INFO level."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        with caplog.at_level("INFO"):
            await policy.on_anthropic_request(request, ctx)

        assert "[ANTHROPIC_REQUEST]" in caplog.text
        assert DEFAULT_TEST_MODEL in caplog.text

    @pytest.mark.asyncio
    async def test_on_anthropic_response_logs_at_info_level(self, caplog):
        """on_anthropic_response logs response data at INFO level."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with caplog.at_level("INFO"):
            await policy.on_anthropic_response(response, ctx)

        assert "[ANTHROPIC_RESPONSE]" in caplog.text
        assert "msg_123" in caplog.text

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_logs_at_info_level(self, caplog):
        """on_anthropic_stream_event logs event data at INFO level."""
        policy = DebugLoggingPolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="Hello"),
        )

        with caplog.at_level("INFO"):
            await policy.on_anthropic_stream_event(event, ctx)

        assert "[ANTHROPIC_STREAM_EVENT]" in caplog.text
        assert "content_block_delta" in caplog.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
