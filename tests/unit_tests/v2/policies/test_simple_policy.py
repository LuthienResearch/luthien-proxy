"""Unit tests for SimplePolicy block-based behavior.

Tests enforce that SimplePolicy:
1. Does NOT emit chunks during on_content_delta / on_tool_call_delta
2. DOES emit complete blocks during on_content_complete / on_tool_call_complete
3. Passes through metadata chunks immediately
4. Only transforms when transformation is needed
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, Function, ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies import PolicyContext
from luthien_proxy.v2.policies.simple_policy import SimplePolicy
from luthien_proxy.v2.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock
from luthien_proxy.v2.streaming.stream_state import StreamState
from luthien_proxy.v2.streaming.streaming_policy_context import StreamingPolicyContext

if TYPE_CHECKING:
    pass


class NoTransformPolicy(SimplePolicy):
    """SimplePolicy subclass that doesn't transform anything."""

    pass


class UppercasePolicy(SimplePolicy):
    """SimplePolicy subclass that uppercases content."""

    async def on_response_content(self, content: str, request: Request) -> str:
        """Transform content to uppercase."""
        return content.upper()


class ToolBlockerPolicy(SimplePolicy):
    """SimplePolicy subclass that blocks tool calls."""

    async def on_response_tool_call(
        self, tool_call: ChatCompletionMessageToolCall, request: Request
    ) -> ChatCompletionMessageToolCall:
        """Transform tool call to blocked function."""
        return ChatCompletionMessageToolCall(
            id=tool_call.id,
            type="function",
            function=Function(name="blocked", arguments="{}"),
        )


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

    # Create stream state (renamed from ingress_state)
    ctx.original_streaming_response_state = StreamState()
    ctx.original_streaming_response_state.just_completed = just_completed
    ctx.original_streaming_response_state.raw_chunks = raw_chunks or []

    # Egress queue and observability
    ctx.egress_queue = AsyncMock()
    ctx.observability = Mock()

    return ctx


class TestSimplePolicyChunkBehavior:
    """Test that SimplePolicy does NOT emit during chunk events."""

    @pytest.mark.asyncio
    async def test_on_content_delta_does_not_emit(self):
        """Test that on_content_delta does not emit any chunks."""
        policy = NoTransformPolicy()
        ctx = create_mock_context()

        # Call on_content_delta
        await policy.on_content_delta(ctx)

        # Verify NO chunks were sent to egress
        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_tool_call_delta_does_not_emit(self):
        """Test that on_tool_call_delta does not emit any chunks."""
        policy = NoTransformPolicy()
        ctx = create_mock_context()

        # Call on_tool_call_delta
        await policy.on_tool_call_delta(ctx)

        # Verify NO chunks were sent to egress
        ctx.egress_queue.put.assert_not_called()


class TestSimplePolicyContentComplete:
    """Test that SimplePolicy emits complete content blocks correctly."""

    @pytest.mark.asyncio
    async def test_on_content_complete_with_no_transform(self):
        """Test that content is passed through when no transformation occurs."""
        policy = NoTransformPolicy()

        # Create a completed content block
        block = ContentStreamBlock(id="content")
        block.content = "hello world"
        block.is_complete = True

        # Create corresponding raw chunks
        raw_chunks = [
            ModelResponse(
                id="test",
                object="chat.completion.chunk",
                created=123,
                model="test",
                choices=[{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}],
            ),
            ModelResponse(
                id="test",
                object="chat.completion.chunk",
                created=123,
                model="test",
                choices=[{"index": 0, "delta": {"content": " world"}, "finish_reason": None}],
            ),
        ]

        ctx = create_mock_context(just_completed=block, raw_chunks=raw_chunks)

        # Call on_content_complete
        await policy.on_content_complete(ctx)

        # Verify accumulated chunks were passed through
        # The passthrough_accumulated_chunks helper should have been called
        assert ctx.egress_queue.put.called

    @pytest.mark.asyncio
    async def test_on_content_complete_with_transform(self):
        """Test that transformed content is emitted when transformation occurs."""
        policy = UppercasePolicy()

        # Create a completed content block
        block = ContentStreamBlock(id="content")
        block.content = "hello"
        block.is_complete = True

        raw_chunks = [
            ModelResponse(
                id="test",
                object="chat.completion.chunk",
                created=123,
                model="test",
                choices=[{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}],
            )
        ]

        ctx = create_mock_context(just_completed=block, raw_chunks=raw_chunks)

        # Call on_content_complete
        await policy.on_content_complete(ctx)

        # Verify that send_text was called (which emits transformed content)
        assert ctx.egress_queue.put.called

    @pytest.mark.asyncio
    async def test_on_content_complete_ignores_non_content_blocks(self):
        """Test that on_content_complete ignores non-ContentStreamBlock completions."""
        policy = NoTransformPolicy()

        # Create a tool call block (not content)
        block = ToolCallStreamBlock(id="call-123", index=0, name="test", arguments="{}")
        block.is_complete = True

        ctx = create_mock_context(just_completed=block)

        # Call on_content_complete
        await policy.on_content_complete(ctx)

        # Verify nothing was emitted
        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_content_complete_with_none_just_completed_logs_and_returns(self):
        """Test that on_content_complete logs error and returns when just_completed is None."""
        policy = NoTransformPolicy()
        ctx = create_mock_context(just_completed=None)

        # Call on_content_complete - should log error and return (not raise)
        await policy.on_content_complete(ctx)

        # Verify nothing was emitted
        ctx.egress_queue.put.assert_not_called()


class TestSimplePolicyToolCallComplete:
    """Test that SimplePolicy emits complete tool call blocks correctly."""

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_with_no_transform(self):
        """Test that tool call is passed through when no transformation occurs."""
        policy = NoTransformPolicy()

        # Create a completed tool call block
        block = ToolCallStreamBlock(id="call-123", index=0, name="get_weather", arguments='{"location": "NYC"}')
        block.is_complete = True

        # Create corresponding raw chunks
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
            ),
        ]

        ctx = create_mock_context(just_completed=block, raw_chunks=raw_chunks)

        # Call on_tool_call_complete
        await policy.on_tool_call_complete(ctx)

        # Verify accumulated chunks were passed through
        assert ctx.egress_queue.put.called

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_with_transform(self):
        """Test that transformed tool call is emitted when transformation occurs."""
        policy = ToolBlockerPolicy()

        # Create a completed tool call block
        block = ToolCallStreamBlock(id="call-123", index=0, name="dangerous_function", arguments='{"do_bad": true}')
        block.is_complete = True

        ctx = create_mock_context(just_completed=block, raw_chunks=[])

        # Call on_tool_call_complete
        await policy.on_tool_call_complete(ctx)

        # Verify that send_tool_call was called (which emits transformed tool call)
        assert ctx.egress_queue.put.called

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_ignores_non_tool_call_blocks(self):
        """Test that on_tool_call_complete ignores non-ToolCallStreamBlock completions."""
        policy = NoTransformPolicy()

        # Create a content block (not tool call)
        block = ContentStreamBlock(id="content")
        block.content = "hello"
        block.is_complete = True

        ctx = create_mock_context(just_completed=block)

        # Call on_tool_call_complete
        await policy.on_tool_call_complete(ctx)

        # Verify nothing was emitted
        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_with_none_just_completed_raises(self):
        """Test that on_tool_call_complete raises error when just_completed is None."""
        policy = NoTransformPolicy()
        ctx = create_mock_context(just_completed=None)

        # Call on_tool_call_complete should raise RuntimeError
        with pytest.raises(RuntimeError):
            await policy.on_tool_call_complete(ctx)


class TestSimplePolicyChunkReceived:
    """Test that SimplePolicy buffers all chunks in on_chunk_received."""

    @pytest.mark.asyncio
    async def test_on_chunk_received_does_not_emit(self):
        """Test that on_chunk_received does not emit any chunks."""
        policy = NoTransformPolicy()

        # Create various types of chunks - none should be emitted
        chunks = [
            # Metadata chunk (role)
            ModelResponse(
                id="test",
                object="chat.completion.chunk",
                created=123,
                model="test",
                choices=[{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            ),
            # Content chunk
            ModelResponse(
                id="test",
                object="chat.completion.chunk",
                created=123,
                model="test",
                choices=[{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}],
            ),
            # Tool call chunk
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
                                    "function": {"name": "test"},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            ),
        ]

        for chunk in chunks:
            ctx = create_mock_context(raw_chunks=[chunk])
            await policy.on_chunk_received(ctx)
            # Verify nothing was emitted
            ctx.egress_queue.put.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
