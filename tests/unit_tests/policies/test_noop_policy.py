"""Unit tests for NoOpPolicy.

Tests verify that NoOpPolicy:
1. Inherits from BasePolicy and implements both OpenAI and Anthropic interfaces
2. Passes through OpenAI requests and responses unchanged
3. Passes through Anthropic requests and responses unchanged
4. Passes through all streaming events unchanged
"""

from __future__ import annotations

from unittest.mock import Mock

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
from litellm.types.utils import ModelResponse
from tests.constants import DEFAULT_CLAUDE_TEST_MODEL

from luthien_proxy.llm.types import Request
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

# =============================================================================
# Protocol and inheritance tests
# =============================================================================


class TestNoOpPolicyInheritance:
    """Tests verifying NoOpPolicy inherits from correct classes."""

    def test_inherits_from_base_policy(self):
        """NoOpPolicy inherits from BasePolicy."""
        assert issubclass(NoOpPolicy, BasePolicy)

    def test_inherits_from_openai_interface(self):
        """NoOpPolicy inherits from OpenAIPolicyInterface."""
        assert issubclass(NoOpPolicy, OpenAIPolicyInterface)

    def test_inherits_from_anthropic_interface(self):
        """NoOpPolicy inherits from AnthropicPolicyInterface."""
        assert issubclass(NoOpPolicy, AnthropicPolicyInterface)

    def test_instantiation(self):
        """NoOpPolicy can be instantiated."""
        policy = NoOpPolicy()
        assert policy is not None
        assert isinstance(policy, BasePolicy)
        assert isinstance(policy, OpenAIPolicyInterface)
        assert isinstance(policy, AnthropicPolicyInterface)

    def test_short_policy_name(self):
        """NoOpPolicy has correct short_policy_name."""
        policy = NoOpPolicy()
        assert policy.short_policy_name == "NoOp"


# =============================================================================
# OpenAI interface tests
# =============================================================================


class TestNoOpPolicyOpenAIRequest:
    """Tests for OpenAI on_openai_request passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_openai_request_returns_same_request(self):
        """on_openai_request returns the exact same request object."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        request = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
        )

        result = await policy.on_openai_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_openai_request_preserves_all_fields(self):
        """on_openai_request preserves all fields in a complex request."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        request = Request(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "How are you?"},
            ],
            temperature=0.7,
            max_tokens=500,
        )

        result = await policy.on_openai_request(request, ctx)

        assert result.model == "gpt-4"
        assert len(result.messages) == 4
        assert result.temperature == 0.7
        assert result.max_tokens == 500


class TestNoOpPolicyOpenAIResponse:
    """Tests for OpenAI on_openai_response passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_openai_response_returns_same_response(self, make_model_response):
        """on_openai_response returns the exact same response object."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        response = make_model_response(content="Hello!")

        result = await policy.on_openai_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_on_openai_response_preserves_content(self, make_model_response):
        """on_openai_response preserves response content."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        response = make_model_response(content="Complex response text")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "Complex response text"


class TestNoOpPolicyOpenAIStreaming:
    """Tests for OpenAI streaming hooks."""

    @pytest.mark.asyncio
    async def test_on_chunk_received_pushes_chunk(self):
        """on_chunk_received pushes the chunk to output."""
        policy = NoOpPolicy()

        chunk = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="gpt-4",
            choices=[{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}],
        )

        ctx = Mock(spec=StreamingPolicyContext)
        ctx.last_chunk_received = chunk

        await policy.on_chunk_received(ctx)

        ctx.push_chunk.assert_called_once_with(chunk)

    @pytest.mark.asyncio
    async def test_on_content_delta_is_noop(self):
        """on_content_delta does nothing."""
        policy = NoOpPolicy()
        ctx = Mock(spec=StreamingPolicyContext)

        await policy.on_content_delta(ctx)

        # Should not call any methods on the context
        ctx.push_chunk.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_content_complete_is_noop(self):
        """on_content_complete does nothing."""
        policy = NoOpPolicy()
        ctx = Mock(spec=StreamingPolicyContext)

        await policy.on_content_complete(ctx)

        ctx.push_chunk.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_tool_call_delta_is_noop(self):
        """on_tool_call_delta does nothing."""
        policy = NoOpPolicy()
        ctx = Mock(spec=StreamingPolicyContext)

        await policy.on_tool_call_delta(ctx)

        ctx.push_chunk.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_is_noop(self):
        """on_tool_call_complete does nothing."""
        policy = NoOpPolicy()
        ctx = Mock(spec=StreamingPolicyContext)

        await policy.on_tool_call_complete(ctx)

        ctx.push_chunk.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_finish_reason_is_noop(self):
        """on_finish_reason does nothing."""
        policy = NoOpPolicy()
        ctx = Mock(spec=StreamingPolicyContext)

        await policy.on_finish_reason(ctx)

        ctx.push_chunk.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_stream_complete_is_noop(self):
        """on_stream_complete does nothing."""
        policy = NoOpPolicy()
        ctx = Mock(spec=StreamingPolicyContext)

        await policy.on_stream_complete(ctx)

        ctx.push_chunk.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_streaming_policy_complete_is_noop(self):
        """on_streaming_policy_complete does nothing."""
        policy = NoOpPolicy()
        ctx = Mock(spec=StreamingPolicyContext)

        await policy.on_streaming_policy_complete(ctx)

        ctx.push_chunk.assert_not_called()


# =============================================================================
# Anthropic interface tests
# =============================================================================


class TestNoOpPolicyAnthropicRequest:
    """Tests for Anthropic on_anthropic_request passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_request_returns_same_request(self):
        """on_anthropic_request returns the exact same request object."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        request = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_anthropic_request_preserves_all_fields(self):
        """on_anthropic_request preserves all fields in a complex request."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        request = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
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

        assert result["model"] == DEFAULT_CLAUDE_TEST_MODEL
        assert len(result["messages"]) == 3
        assert result["max_tokens"] == 500
        assert result.get("temperature") == 0.7
        assert result.get("system") == "You are a helpful assistant."


class TestNoOpPolicyAnthropicResponse:
    """Tests for Anthropic on_anthropic_response passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_response_returns_same_response(self):
        """on_anthropic_response returns the exact same response object."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        response = Message.model_construct(
            id="msg_123",
            type="message",
            role="assistant",
            content=[TextBlock.model_construct(type="text", text="Hello!")],
            model=DEFAULT_CLAUDE_TEST_MODEL,
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 5},
        )

        result = await policy.on_anthropic_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_on_anthropic_response_preserves_content(self):
        """on_anthropic_response preserves content blocks exactly."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        response = Message.model_construct(
            id="msg_456",
            type="message",
            role="assistant",
            content=[TextBlock.model_construct(type="text", text="Complex response text")],
            model=DEFAULT_CLAUDE_TEST_MODEL,
            stop_reason="end_turn",
            usage={"input_tokens": 20, "output_tokens": 10},
        )

        result = await policy.on_anthropic_response(response, ctx)

        assert result.content[0].text == "Complex response text"
        assert result.usage.input_tokens == 20
        assert result.usage.output_tokens == 10


class TestNoOpPolicyAnthropicStreaming:
    """Tests for Anthropic on_anthropic_stream_event passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_returns_same_event(self):
        """on_anthropic_stream_event returns the exact same event object."""
        policy = NoOpPolicy()
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
        """on_anthropic_stream_event never filters out events."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        events = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": DEFAULT_CLAUDE_TEST_MODEL,
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
    async def test_on_anthropic_stream_event_handles_all_event_types(self):
        """on_anthropic_stream_event handles all Anthropic stream event types."""
        policy = NoOpPolicy()
        ctx = PolicyContext.for_testing()

        message_start = RawMessageStartEvent.model_construct(
            type="message_start",
            message={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": DEFAULT_CLAUDE_TEST_MODEL,
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
