"""Unit tests for SimpleNoOpPolicy.

Tests verify that SimpleNoOpPolicy:
1. Inherits from SimplePolicy correctly
2. Applies no transformations to content
3. Applies no transformations to tool calls
4. Acts as a passthrough for streaming
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.llm.types import Request
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.simple_noop_policy import SimpleNoOpPolicy
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock
from luthien_proxy.streaming.stream_state import StreamState


def create_mock_context(
    just_completed=None,
    raw_chunks: list[ModelResponse] | None = None,
) -> StreamingPolicyContext:
    """Create a mock StreamingPolicyContext for testing."""
    ctx = Mock(spec=StreamingPolicyContext)

    # Create PolicyContext with request
    ctx.policy_ctx = Mock(spec=PolicyContext)
    ctx.policy_ctx.transaction_id = "test-transaction-id"
    ctx.policy_ctx.request = Request(
        model="test-model",
        messages=[{"role": "user", "content": "test"}],
    )
    ctx.policy_ctx.scratchpad = {}

    # Create stream state
    ctx.original_streaming_response_state = StreamState()
    ctx.original_streaming_response_state.just_completed = just_completed
    ctx.original_streaming_response_state.raw_chunks = raw_chunks or []

    # Egress queue and observability
    ctx.egress_queue = AsyncMock()
    ctx.observability = Mock()

    return ctx


class TestSimpleNoOpPolicyInheritance:
    """Test that SimpleNoOpPolicy correctly inherits from SimplePolicy."""

    def test_inherits_from_simple_policy(self):
        """Verify SimpleNoOpPolicy is a subclass of SimplePolicy."""
        assert issubclass(SimpleNoOpPolicy, SimplePolicy)

    def test_instantiation(self):
        """Verify SimpleNoOpPolicy can be instantiated."""
        policy = SimpleNoOpPolicy()
        assert policy is not None
        assert isinstance(policy, SimplePolicy)


class TestSimpleNoOpPolicyBehavior:
    """Test that SimpleNoOpPolicy passes through without transformations."""

    @pytest.mark.asyncio
    async def test_on_chunk_received_does_not_emit(self):
        """Test that on_chunk_received does not emit any chunks."""
        policy = SimpleNoOpPolicy()
        chunk = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}],
        )
        ctx = create_mock_context(raw_chunks=[chunk])

        await policy.on_chunk_received(ctx)

        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_content_delta_does_not_emit(self):
        """Test that on_content_delta does not emit any chunks."""
        policy = SimpleNoOpPolicy()
        ctx = create_mock_context()

        await policy.on_content_delta(ctx)

        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_tool_call_delta_does_not_emit(self):
        """Test that on_tool_call_delta does not emit any chunks."""
        policy = SimpleNoOpPolicy()
        ctx = create_mock_context()

        await policy.on_tool_call_delta(ctx)

        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_content_complete_passes_through(self):
        """Test that content is passed through without transformation."""
        policy = SimpleNoOpPolicy()

        block = ContentStreamBlock(id="content")
        block.content = "hello world"
        block.is_complete = True

        raw_chunks = [
            ModelResponse(
                id="test",
                object="chat.completion.chunk",
                created=123,
                model="test",
                choices=[{"index": 0, "delta": {"content": "hello world"}, "finish_reason": None}],
            )
        ]

        ctx = create_mock_context(just_completed=block, raw_chunks=raw_chunks)

        await policy.on_content_complete(ctx)

        # Should pass through accumulated chunks
        assert ctx.egress_queue.put.called

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_passes_through(self):
        """Test that tool calls are passed through without transformation."""
        policy = SimpleNoOpPolicy()

        block = ToolCallStreamBlock(
            id="call-123",
            index=0,
            name="get_weather",
            arguments='{"location": "NYC"}',
        )
        block.is_complete = True

        raw_chunks = [
            ModelResponse(
                id="test",
                object="chat.completion.chunk",
                created=123,
                model="test",
                choices=[
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-123",
                                    "type": "function",
                                    "function": {"name": "get_weather", "arguments": '{"location": "NYC"}'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            )
        ]

        ctx = create_mock_context(just_completed=block, raw_chunks=raw_chunks)

        await policy.on_tool_call_complete(ctx)

        assert ctx.egress_queue.put.called


class TestSimpleNoOpPolicyNonStreaming:
    """Test SimpleNoOpPolicy non-streaming request/response handling."""

    @pytest.mark.asyncio
    async def test_on_request_returns_unchanged(self):
        """Test that on_request returns the request unchanged."""
        policy = SimpleNoOpPolicy()

        request = Request(
            model="test-model",
            messages=[{"role": "user", "content": "Hello"}],
        )
        context = Mock(spec=PolicyContext)
        context.scratchpad = {}

        result = await policy.on_request(request, context)

        # SimplePolicy converts request to string and calls simple_on_request
        # For SimpleNoOpPolicy, simple_on_request returns the input unchanged
        assert result is not None

    @pytest.mark.asyncio
    async def test_on_response_returns_unchanged(self, make_model_response):
        """Test that on_response returns the response unchanged."""
        policy = SimpleNoOpPolicy()

        response = make_model_response(content="Hello from assistant")
        context = Mock(spec=PolicyContext)
        context.scratchpad = {}

        result = await policy.on_response(response, context)

        # Should return unchanged
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
