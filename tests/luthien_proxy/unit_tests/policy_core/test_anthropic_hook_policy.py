"""Tests for AnthropicHookPolicy default hook-based implementation."""

from __future__ import annotations

import pytest

from luthien_proxy.policy_core.anthropic_hook_policy import AnthropicHookPolicy
from luthien_proxy.policy_core.policy_context import PolicyContext


class TestAnthropicHookPolicyDefaults:
    """Test default hook behaviors of AnthropicHookPolicy."""

    @pytest.mark.asyncio
    async def test_on_anthropic_request_default_passthrough(self):
        """Default on_anthropic_request returns request unchanged."""
        policy = AnthropicHookPolicy()
        ctx = PolicyContext.for_testing()
        request = {"model": "claude-3-5-sonnet", "messages": [], "max_tokens": 100}

        result = await policy.on_anthropic_request(request, ctx)

        assert result == request

    @pytest.mark.asyncio
    async def test_on_anthropic_response_default_passthrough(self):
        """Default on_anthropic_response returns response unchanged."""
        policy = AnthropicHookPolicy()
        ctx = PolicyContext.for_testing()
        response = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "model": "claude-3-5-sonnet",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result == response

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_default_returns_single_event(self):
        """Default on_anthropic_stream_event returns event in a list."""
        from anthropic.types import RawContentBlockDeltaEvent, TextDelta

        policy = AnthropicHookPolicy()
        ctx = PolicyContext.for_testing()
        event = RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=TextDelta(type="text_delta", text="hello"),
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_complete_default_returns_empty(self):
        """Default on_anthropic_stream_complete returns empty list."""
        policy = AnthropicHookPolicy()
        ctx = PolicyContext.for_testing()

        result = await policy.on_anthropic_stream_complete(ctx)

        assert result == []
