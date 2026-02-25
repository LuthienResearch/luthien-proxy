"""Unit tests for DebugLoggingPolicy.

Tests cover:
1. Policy name and interface inheritance
2. OpenAI interface: on_openai_request, on_chunk_received, on_openai_response
3. Anthropic interface: on_anthropic_request, on_anthropic_response, on_anthropic_stream_event
4. Event recording for DB persistence
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

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
from litellm.types.utils import ModelResponse

from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
)
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.debug_logging_policy import DebugLoggingPolicy
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_state import StreamState
from luthien_proxy.types import RawHttpRequest


def create_mock_policy_context(
    raw_http_request: RawHttpRequest | None = None,
) -> PolicyContext:
    """Create a mock PolicyContext for testing."""
    ctx = Mock(spec=PolicyContext)
    ctx.transaction_id = "test-transaction-id"
    ctx.raw_http_request = raw_http_request
    ctx.scratchpad = {}
    ctx.record_event = Mock()
    return ctx


def create_mock_streaming_context(
    raw_chunks: list[ModelResponse] | None = None,
) -> StreamingPolicyContext:
    """Create a mock StreamingPolicyContext for testing."""
    ctx = Mock(spec=StreamingPolicyContext)

    ctx.original_streaming_response_state = StreamState()
    ctx.original_streaming_response_state.raw_chunks = raw_chunks or []

    ctx.egress_queue = asyncio.Queue()
    ctx.observability = Mock()

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

    def test_implements_openai_interface(self):
        """Test that DebugLoggingPolicy implements OpenAIPolicyInterface."""
        policy = DebugLoggingPolicy()
        assert isinstance(policy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self):
        """Test that DebugLoggingPolicy implements AnthropicPolicyInterface."""
        policy = DebugLoggingPolicy()
        assert isinstance(policy, AnthropicPolicyInterface)


# =============================================================================
# OpenAI Interface Tests
# =============================================================================


class TestDebugLoggingPolicyOpenAIRequest:
    """Test on_openai_request method."""

    @pytest.mark.asyncio
    async def test_on_openai_request_with_raw_http_request(self):
        """Test on_openai_request logs raw HTTP request when available."""
        policy = DebugLoggingPolicy()

        raw_request = RawHttpRequest(
            method="POST",
            path="/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            body={"model": "gpt-4", "messages": []},
        )

        request = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
        )

        context = create_mock_policy_context(raw_http_request=raw_request)

        with patch("luthien_proxy.policies.debug_logging_policy.logger") as mock_logger:
            result = await policy.on_openai_request(request, context)

        assert result == request

        assert mock_logger.info.call_count >= 3

        context.record_event.assert_called_once()
        call_args = context.record_event.call_args
        assert call_args[0][0] == "debug.raw_http_request"
        assert call_args[0][1]["method"] == "POST"
        assert call_args[0][1]["path"] == "/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_on_openai_request_without_raw_http_request(self):
        """Test on_openai_request warns when raw HTTP request is not available."""
        policy = DebugLoggingPolicy()

        request = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
        )

        context = create_mock_policy_context(raw_http_request=None)

        with patch("luthien_proxy.policies.debug_logging_policy.logger") as mock_logger:
            result = await policy.on_openai_request(request, context)

        assert result == request

        mock_logger.warning.assert_called_once()
        assert "No raw HTTP request" in mock_logger.warning.call_args[0][0]

        context.record_event.assert_not_called()


class TestDebugLoggingPolicyOpenAIChunk:
    """Test on_chunk_received method."""

    @pytest.mark.asyncio
    async def test_on_chunk_received_logs_and_passes_through(self):
        """Test on_chunk_received logs chunk and passes it through."""
        policy = DebugLoggingPolicy()

        chunk = ModelResponse(
            id="test-chunk",
            object="chat.completion.chunk",
            created=123456,
            model="gpt-4",
            choices=[
                {
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        )

        ctx = create_mock_streaming_context(raw_chunks=[chunk])

        with patch("luthien_proxy.policies.debug_logging_policy.logger") as mock_logger:
            await policy.on_chunk_received(ctx)

        mock_logger.info.assert_called()
        log_message = mock_logger.info.call_args_list[0][0][0]
        assert "[CHUNK]" in log_message

        assert not ctx.egress_queue.empty()
        passed_chunk = ctx.egress_queue.get_nowait()
        assert passed_chunk.id == "test-chunk"

    @pytest.mark.asyncio
    async def test_on_chunk_received_logs_hidden_params(self):
        """Test on_chunk_received logs hidden params if they exist."""
        policy = DebugLoggingPolicy()

        chunk = ModelResponse(
            id="test-chunk",
            object="chat.completion.chunk",
            created=123456,
            model="gpt-4",
            choices=[
                {
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        )
        chunk._hidden_params = {"custom_key": "custom_value"}

        ctx = create_mock_streaming_context(raw_chunks=[chunk])

        with patch("luthien_proxy.policies.debug_logging_policy.logger") as mock_logger:
            await policy.on_chunk_received(ctx)

        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("[HIDDEN_PARAMS]" in call for call in log_calls)


class TestDebugLoggingPolicyOpenAIResponse:
    """Test on_openai_response method."""

    @pytest.mark.asyncio
    async def test_on_openai_response_passes_through(self, make_model_response):
        """Test on_openai_response passes response through unchanged."""
        policy = DebugLoggingPolicy()

        response = make_model_response(content="Hello from assistant")
        context = Mock()

        result = await policy.on_openai_response(response, context)

        assert result == response
        assert result.choices[0].message.content == "Hello from assistant"


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
            "model": "claude-sonnet-4-20250514",
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

        result = await policy.on_anthropic_request(request, ctx)

        assert result["model"] == "claude-sonnet-4-20250514"
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
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "system": "You are helpful.",
        }

        await policy.on_anthropic_request(request, ctx)

        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        assert call_args[0][0] == "debug.anthropic_request"
        assert call_args[0][1]["model"] == "claude-sonnet-4-20250514"
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
            "model": "claude-sonnet-4-20250514",
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
            "model": "claude-sonnet-4-20250514",
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
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        await policy.on_anthropic_response(response, ctx)

        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        assert call_args[0][0] == "debug.anthropic_response"
        assert call_args[0][1]["id"] == "msg_789"
        assert call_args[0][1]["model"] == "claude-sonnet-4-20250514"
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

        assert await policy.on_anthropic_stream_event(message_start, ctx) == [message_start]
        assert await policy.on_anthropic_stream_event(content_block_start, ctx) == [content_block_start]
        assert await policy.on_anthropic_stream_event(content_block_delta, ctx) == [content_block_delta]
        assert await policy.on_anthropic_stream_event(content_block_stop, ctx) == [content_block_stop]
        assert await policy.on_anthropic_stream_event(message_delta, ctx) == [message_delta]
        assert await policy.on_anthropic_stream_event(message_stop, ctx) == [message_stop]


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
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        with caplog.at_level("INFO"):
            await policy.on_anthropic_request(request, ctx)

        assert "[ANTHROPIC_REQUEST]" in caplog.text
        assert "claude-sonnet-4-20250514" in caplog.text

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
            "model": "claude-sonnet-4-20250514",
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
