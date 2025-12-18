"""Unit tests for BasePolicy default implementations.

Tests verify that BasePolicy:
1. Provides pass-through implementations for all protocol methods
2. Returns default short_policy_name
3. Pushes chunks through on_chunk_received
4. Provides no-op implementations for all event callbacks
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.llm.types import Request
from luthien_proxy.policies.base_policy import BasePolicy
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext


def create_mock_policy_context() -> PolicyContext:
    """Create a mock PolicyContext for testing."""
    ctx = Mock(spec=PolicyContext)
    ctx.transaction_id = "test-transaction"
    ctx.request = Request(
        model="test-model",
        messages=[{"role": "user", "content": "test"}],
    )
    ctx.scratchpad = {}
    return ctx


def create_mock_streaming_context() -> StreamingPolicyContext:
    """Create a mock StreamingPolicyContext for testing."""
    ctx = Mock(spec=StreamingPolicyContext)
    ctx.policy_ctx = create_mock_policy_context()

    # Mock the last_chunk_received
    ctx.last_chunk_received = ModelResponse(
        id="test",
        object="chat.completion.chunk",
        created=123,
        model="test",
        choices=[{"index": 0, "delta": {"content": "test"}, "finish_reason": None}],
    )

    # Mock push_chunk method
    ctx.push_chunk = Mock()

    return ctx


class TestBasePolicyProperties:
    """Test BasePolicy property defaults."""

    def test_short_policy_name_returns_class_name(self):
        """Test that short_policy_name defaults to class name."""
        policy = BasePolicy()
        assert policy.short_policy_name == "BasePolicy"

    def test_short_policy_name_for_subclass(self):
        """Test that short_policy_name works for subclasses."""

        class CustomPolicy(BasePolicy):
            pass

        policy = CustomPolicy()
        assert policy.short_policy_name == "CustomPolicy"


class TestBasePolicyRequestResponse:
    """Test BasePolicy request/response pass-through behavior."""

    @pytest.mark.asyncio
    async def test_on_request_passes_through_unchanged(self):
        """Test that on_request returns the same request."""
        policy = BasePolicy()
        ctx = create_mock_policy_context()

        request = Request(
            model="test-model",
            messages=[{"role": "user", "content": "hello"}],
        )

        result = await policy.on_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_response_passes_through_unchanged(self):
        """Test that on_response returns the same response."""
        policy = BasePolicy()
        ctx = create_mock_policy_context()

        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
        )

        result = await policy.on_response(response, ctx)

        assert result is response


class TestBasePolicyStreamingCallbacks:
    """Test BasePolicy streaming callback default implementations."""

    @pytest.mark.asyncio
    async def test_on_chunk_received_pushes_chunk(self):
        """Test that on_chunk_received pushes the last received chunk."""
        policy = BasePolicy()
        ctx = create_mock_streaming_context()

        await policy.on_chunk_received(ctx)

        # Verify push_chunk was called with the last chunk
        ctx.push_chunk.assert_called_once_with(ctx.last_chunk_received)

    @pytest.mark.asyncio
    async def test_on_content_delta_does_nothing(self):
        """Test that on_content_delta is a no-op."""
        policy = BasePolicy()
        ctx = create_mock_streaming_context()

        # Should not raise and should return None
        result = await policy.on_content_delta(ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_on_content_complete_does_nothing(self):
        """Test that on_content_complete is a no-op."""
        policy = BasePolicy()
        ctx = create_mock_streaming_context()

        result = await policy.on_content_complete(ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_on_tool_call_delta_does_nothing(self):
        """Test that on_tool_call_delta is a no-op."""
        policy = BasePolicy()
        ctx = create_mock_streaming_context()

        result = await policy.on_tool_call_delta(ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_does_nothing(self):
        """Test that on_tool_call_complete is a no-op."""
        policy = BasePolicy()
        ctx = create_mock_streaming_context()

        result = await policy.on_tool_call_complete(ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_on_finish_reason_does_nothing(self):
        """Test that on_finish_reason is a no-op."""
        policy = BasePolicy()
        ctx = create_mock_streaming_context()

        result = await policy.on_finish_reason(ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_on_stream_complete_does_nothing(self):
        """Test that on_stream_complete is a no-op."""
        policy = BasePolicy()
        ctx = create_mock_streaming_context()

        result = await policy.on_stream_complete(ctx)
        assert result is None


class TestBasePolicyInheritance:
    """Test that BasePolicy can be properly subclassed."""

    @pytest.mark.asyncio
    async def test_subclass_can_override_on_request(self):
        """Test that subclasses can override on_request."""

        class ModifyingPolicy(BasePolicy):
            async def on_request(self, request: Request, context: PolicyContext) -> Request:
                # Modify the request
                modified = Request(
                    model=request.model,
                    messages=[{"role": "system", "content": "prefix"}] + request.messages,
                )
                return modified

        policy = ModifyingPolicy()
        ctx = create_mock_policy_context()

        request = Request(
            model="test-model",
            messages=[{"role": "user", "content": "hello"}],
        )

        result = await policy.on_request(request, ctx)

        assert len(result.messages) == 2
        assert result.messages[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_subclass_can_override_streaming_callback(self):
        """Test that subclasses can override streaming callbacks."""

        class CountingPolicy(BasePolicy):
            def __init__(self):
                self.chunk_count = 0

            async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
                self.chunk_count += 1
                await super().on_chunk_received(ctx)

        policy = CountingPolicy()
        ctx = create_mock_streaming_context()

        await policy.on_chunk_received(ctx)
        await policy.on_chunk_received(ctx)

        assert policy.chunk_count == 2
        assert ctx.push_chunk.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
