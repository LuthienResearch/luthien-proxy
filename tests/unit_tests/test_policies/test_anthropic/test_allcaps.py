# ABOUTME: Tests for AnthropicAllCapsPolicy verifying uppercase transformation behavior
"""Tests for AnthropicAllCapsPolicy.

Verifies that AnthropicAllCapsPolicy:
1. Implements the AnthropicPolicyProtocol
2. Passes through requests unchanged
3. Transforms text content to uppercase in non-streaming responses
4. Leaves non-text content unchanged in non-streaming responses
5. Transforms text deltas to uppercase in streaming responses
6. Leaves non-text streaming events unchanged
"""

from typing import cast

import pytest

from luthien_proxy.llm.types.anthropic import (
    AnthropicContentBlockDeltaEvent,
    AnthropicContentBlockStartEvent,
    AnthropicContentBlockStopEvent,
    AnthropicInputJSONDelta,
    AnthropicMessageDeltaEvent,
    AnthropicMessageStartEvent,
    AnthropicMessageStopEvent,
    AnthropicPingEvent,
    AnthropicRequest,
    AnthropicResponse,
    AnthropicStreamingEvent,
    AnthropicTextBlock,
    AnthropicTextDelta,
    AnthropicThinkingDelta,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies.anthropic.allcaps import AnthropicAllCapsPolicy
from luthien_proxy.policy_core.anthropic_protocol import AnthropicPolicyProtocol
from luthien_proxy.policy_core.policy_context import PolicyContext


class TestAnthropicAllCapsPolicyProtocol:
    """Tests verifying AnthropicAllCapsPolicy implements the protocol."""

    def test_implements_protocol(self):
        """AnthropicAllCapsPolicy satisfies AnthropicPolicyProtocol."""
        policy = AnthropicAllCapsPolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)

    def test_has_short_policy_name(self):
        """AnthropicAllCapsPolicy has correct short_policy_name property."""
        policy = AnthropicAllCapsPolicy()
        assert policy.short_policy_name == "AnthropicAllCaps"


class TestAnthropicAllCapsPolicyRequest:
    """Tests for on_request passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_request_returns_same_request(self):
        """on_request returns the exact same request object unchanged."""
        policy = AnthropicAllCapsPolicy()
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
        policy = AnthropicAllCapsPolicy()
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


class TestAnthropicAllCapsPolicyResponse:
    """Tests for on_response text transformation behavior."""

    @pytest.mark.asyncio
    async def test_on_response_transforms_text_to_uppercase(self):
        """on_response converts text content blocks to uppercase."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello, world!"}
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

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "HELLO, WORLD!"

    @pytest.mark.asyncio
    async def test_on_response_transforms_multiple_text_blocks(self):
        """on_response transforms all text blocks to uppercase."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        text_block1: AnthropicTextBlock = {"type": "text", "text": "First block"}
        text_block2: AnthropicTextBlock = {"type": "text", "text": "Second block"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block1, text_block2],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

        result = await policy.on_response(response, ctx)

        result_text_block0 = cast(AnthropicTextBlock, result["content"][0])
        result_text_block1 = cast(AnthropicTextBlock, result["content"][1])
        assert result_text_block0["text"] == "FIRST BLOCK"
        assert result_text_block1["text"] == "SECOND BLOCK"

    @pytest.mark.asyncio
    async def test_on_response_leaves_tool_use_unchanged(self):
        """on_response does not modify tool_use content blocks."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "get_weather",
            "input": {"location": "San Francisco"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_use_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_response(response, ctx)

        result_tool_block = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"
        assert result_tool_block["input"] == {"location": "San Francisco"}

    @pytest.mark.asyncio
    async def test_on_response_mixed_content_blocks(self):
        """on_response transforms text but leaves tool_use unchanged in mixed content."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Let me check the weather"}
        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_456",
            "name": "get_weather",
            "input": {"location": "NYC"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block, tool_use_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 15},
        }

        result = await policy.on_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        result_tool_block = cast(AnthropicToolUseBlock, result["content"][1])
        assert result_text_block["text"] == "LET ME CHECK THE WEATHER"
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_on_response_preserves_usage_and_metadata(self):
        """on_response preserves usage stats and other metadata."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Test"}
        response: AnthropicResponse = {
            "id": "msg_789",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 25, "output_tokens": 15},
        }

        result = await policy.on_response(response, ctx)

        assert result["id"] == "msg_789"
        assert result["model"] == "claude-sonnet-4-20250514"
        assert result.get("stop_reason") == "end_turn"
        assert result["usage"]["input_tokens"] == 25
        assert result["usage"]["output_tokens"] == 15


class TestAnthropicAllCapsPolicyStreamEvent:
    """Tests for on_stream_event text delta transformation behavior."""

    @pytest.mark.asyncio
    async def test_on_stream_event_transforms_text_delta_to_uppercase(self):
        """on_stream_event converts text_delta text to uppercase."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        text_delta: AnthropicTextDelta = {"type": "text_delta", "text": "hello world"}
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": text_delta,
        }

        result = await policy.on_stream_event(event, ctx)

        assert result is not None
        result_delta = cast(AnthropicContentBlockDeltaEvent, result)
        assert result_delta["type"] == "content_block_delta"
        result_text_delta = cast(AnthropicTextDelta, result_delta["delta"])
        assert result_text_delta["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_stream_event_leaves_thinking_delta_unchanged(self):
        """on_stream_event does not modify thinking_delta events."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        thinking_delta: AnthropicThinkingDelta = {"type": "thinking_delta", "thinking": "Let me consider..."}
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": thinking_delta,
        }

        result = await policy.on_stream_event(event, ctx)

        assert result is not None
        result_delta = cast(AnthropicContentBlockDeltaEvent, result)
        result_thinking_delta = cast(AnthropicThinkingDelta, result_delta["delta"])
        assert result_thinking_delta["thinking"] == "Let me consider..."

    @pytest.mark.asyncio
    async def test_on_stream_event_leaves_input_json_delta_unchanged(self):
        """on_stream_event does not modify input_json_delta events."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        json_delta: AnthropicInputJSONDelta = {"type": "input_json_delta", "partial_json": '{"loc'}
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": json_delta,
        }

        result = await policy.on_stream_event(event, ctx)

        assert result is not None
        result_delta = cast(AnthropicContentBlockDeltaEvent, result)
        result_json_delta = cast(AnthropicInputJSONDelta, result_delta["delta"])
        assert result_json_delta["partial_json"] == '{"loc'

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_start(self):
        """on_stream_event passes through message_start events unchanged."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event: AnthropicMessageStartEvent = {
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

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_content_block_start(self):
        """on_stream_event passes through content_block_start events unchanged."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event: AnthropicContentBlockStartEvent = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_content_block_stop(self):
        """on_stream_event passes through content_block_stop events unchanged."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event: AnthropicContentBlockStopEvent = {
            "type": "content_block_stop",
            "index": 0,
        }

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_delta(self):
        """on_stream_event passes through message_delta events unchanged."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event: AnthropicMessageDeltaEvent = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 10},
        }

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_stop(self):
        """on_stream_event passes through message_stop events unchanged."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event: AnthropicMessageStopEvent = {"type": "message_stop"}

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_ping(self):
        """on_stream_event passes through ping events unchanged."""
        policy = AnthropicAllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event: AnthropicPingEvent = {"type": "ping"}

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_never_returns_none(self):
        """on_stream_event never filters out events (returns None)."""
        policy = AnthropicAllCapsPolicy()
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


__all__ = [
    "TestAnthropicAllCapsPolicyProtocol",
    "TestAnthropicAllCapsPolicyRequest",
    "TestAnthropicAllCapsPolicyResponse",
    "TestAnthropicAllCapsPolicyStreamEvent",
]
