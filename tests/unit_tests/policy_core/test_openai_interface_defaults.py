"""Tests for OpenAIPolicyInterface default streaming hook implementations."""

from __future__ import annotations

import asyncio

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_state import StreamState


class MinimalPolicy(OpenAIPolicyInterface):
    """Policy that only implements the required non-streaming methods.

    If defaults work correctly, this should be instantiable and functional
    without implementing any streaming hooks.
    """

    async def on_openai_request(self, request, context):
        return request

    async def on_openai_response(self, response, context):
        return response


def _make_streaming_ctx() -> StreamingPolicyContext:
    """Create a minimal StreamingPolicyContext for testing."""
    stream_state = StreamState()
    policy_ctx = PolicyContext.for_testing()
    egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()
    return StreamingPolicyContext(
        policy_ctx=policy_ctx,
        egress_queue=egress_queue,
        original_streaming_response_state=stream_state,
        keepalive=lambda: None,
    )


class TestOpenAIInterfaceDefaults:
    """Verify that streaming hooks have sensible defaults."""

    def test_minimal_policy_is_instantiable(self):
        """A policy implementing only request/response should be instantiable."""
        policy = MinimalPolicy()
        assert policy is not None

    @pytest.mark.asyncio
    async def test_default_on_chunk_received_forwards_chunk(self):
        """Default on_chunk_received should push the last received chunk."""
        from luthien_proxy.policy_core.chunk_builders import create_text_chunk

        policy = MinimalPolicy()
        ctx = _make_streaming_ctx()

        chunk = create_text_chunk("hello", model="test")
        ctx.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_chunk_received(ctx)

        assert not ctx.egress_queue.empty()
        forwarded = ctx.egress_queue.get_nowait()
        assert forwarded is chunk

    @pytest.mark.asyncio
    async def test_default_on_content_delta_is_noop(self):
        """Default on_content_delta should do nothing."""
        policy = MinimalPolicy()
        ctx = _make_streaming_ctx()
        await policy.on_content_delta(ctx)
        assert ctx.egress_queue.empty()

    @pytest.mark.asyncio
    async def test_default_on_content_complete_is_noop(self):
        policy = MinimalPolicy()
        ctx = _make_streaming_ctx()
        await policy.on_content_complete(ctx)
        assert ctx.egress_queue.empty()

    @pytest.mark.asyncio
    async def test_default_on_tool_call_delta_is_noop(self):
        policy = MinimalPolicy()
        ctx = _make_streaming_ctx()
        await policy.on_tool_call_delta(ctx)
        assert ctx.egress_queue.empty()

    @pytest.mark.asyncio
    async def test_default_on_tool_call_complete_is_noop(self):
        policy = MinimalPolicy()
        ctx = _make_streaming_ctx()
        await policy.on_tool_call_complete(ctx)
        assert ctx.egress_queue.empty()

    @pytest.mark.asyncio
    async def test_default_on_finish_reason_is_noop(self):
        policy = MinimalPolicy()
        ctx = _make_streaming_ctx()
        await policy.on_finish_reason(ctx)
        assert ctx.egress_queue.empty()

    @pytest.mark.asyncio
    async def test_default_on_stream_complete_is_noop(self):
        policy = MinimalPolicy()
        ctx = _make_streaming_ctx()
        await policy.on_stream_complete(ctx)
        assert ctx.egress_queue.empty()

    @pytest.mark.asyncio
    async def test_default_on_streaming_policy_complete_is_noop(self):
        policy = MinimalPolicy()
        ctx = _make_streaming_ctx()
        await policy.on_streaming_policy_complete(ctx)
        assert ctx.egress_queue.empty()
