# ABOUTME: Tests for AnthropicSimplePolicy verifying content-level transformation behavior
"""Tests for AnthropicSimplePolicy.

Verifies that AnthropicSimplePolicy:
1. Implements the AnthropicPolicyProtocol
2. Provides simple hooks for request/response content transformation
3. Buffers streaming deltas and emits transformed content on block completion
4. Handles both text and tool_use content blocks
"""

from typing import cast

import pytest
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolUseBlock,
)

from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies.anthropic.simple_policy import AnthropicSimplePolicy
from luthien_proxy.policy_core.anthropic_protocol import AnthropicPolicyProtocol
from luthien_proxy.policy_core.policy_context import PolicyContext


class UppercaseSimplePolicy(AnthropicSimplePolicy):
    """Test policy that transforms text to uppercase."""

    async def simple_on_request(self, request_text: str, context: PolicyContext) -> str:
        return request_text.upper()

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        return content.upper()


class PrefixToolNamePolicy(AnthropicSimplePolicy):
    """Test policy that prefixes tool names with 'test_'."""

    async def simple_on_response_tool_call(
        self, tool_call: AnthropicToolUseBlock, context: PolicyContext
    ) -> AnthropicToolUseBlock:
        return {
            "type": "tool_use",
            "id": tool_call["id"],
            "name": f"test_{tool_call['name']}",
            "input": tool_call["input"],
        }


class TestAnthropicSimplePolicyProtocol:
    """Tests verifying AnthropicSimplePolicy implements the protocol."""

    def test_implements_protocol(self):
        """AnthropicSimplePolicy satisfies AnthropicPolicyProtocol."""
        policy = AnthropicSimplePolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)

    def test_has_short_policy_name(self):
        """AnthropicSimplePolicy has a short_policy_name property defaulting to class name."""
        policy = AnthropicSimplePolicy()
        assert policy.short_policy_name == "AnthropicSimplePolicy"

    def test_subclass_short_policy_name(self):
        """Subclass uses its own class name for short_policy_name."""
        policy = UppercaseSimplePolicy()
        assert policy.short_policy_name == "UppercaseSimplePolicy"


class TestAnthropicSimplePolicyRequest:
    """Tests for on_request behavior."""

    @pytest.mark.asyncio
    async def test_on_request_passthrough_by_default(self):
        """Base class on_request passes through text unchanged."""
        policy = AnthropicSimplePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello world"}],
            "max_tokens": 100,
        }

        result = await policy.on_request(request, ctx)

        assert result["messages"][-1]["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_on_request_transforms_string_content(self):
        """Subclass simple_on_request transforms string message content."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "hello world"}],
            "max_tokens": 100,
        }

        result = await policy.on_request(request, ctx)

        assert result["messages"][-1]["content"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_request_transforms_text_block_content(self):
        """Subclass simple_on_request transforms text blocks in message content list."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "hello world"}
        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": [text_block]}],
            "max_tokens": 100,
        }

        result = await policy.on_request(request, ctx)

        content_list = cast(list, result["messages"][-1]["content"])
        text_block_result = cast(AnthropicTextBlock, content_list[0])
        assert text_block_result["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_request_empty_messages(self):
        """on_request handles empty messages list gracefully."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [],
            "max_tokens": 100,
        }

        result = await policy.on_request(request, ctx)

        assert result["messages"] == []


class TestAnthropicSimplePolicyResponse:
    """Tests for on_response behavior."""

    @pytest.mark.asyncio
    async def test_on_response_passthrough_by_default(self):
        """Base class on_response passes through content unchanged."""
        policy = AnthropicSimplePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello world"}
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
        assert result_text_block["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_on_response_transforms_text_content(self):
        """Subclass simple_on_response_content transforms text blocks."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "hello world"}
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
        assert result_text_block["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_response_transforms_multiple_text_blocks(self):
        """Subclass transforms all text blocks in response."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        text_block1: AnthropicTextBlock = {"type": "text", "text": "first block"}
        text_block2: AnthropicTextBlock = {"type": "text", "text": "second block"}
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

        result_block0 = cast(AnthropicTextBlock, result["content"][0])
        result_block1 = cast(AnthropicTextBlock, result["content"][1])
        assert result_block0["text"] == "FIRST BLOCK"
        assert result_block1["text"] == "SECOND BLOCK"

    @pytest.mark.asyncio
    async def test_on_response_transforms_tool_calls(self):
        """Subclass simple_on_response_tool_call transforms tool_use blocks."""
        policy = PrefixToolNamePolicy()
        ctx = PolicyContext.for_testing()

        tool_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "get_weather",
            "input": {"location": "NYC"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_response(response, ctx)

        result_tool_block = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool_block["name"] == "test_get_weather"
        assert result_tool_block["input"] == {"location": "NYC"}

    @pytest.mark.asyncio
    async def test_on_response_mixed_content(self):
        """Subclass transforms both text and tool blocks in mixed content."""

        # Create a policy that does both transformations
        class MixedPolicy(AnthropicSimplePolicy):
            async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
                return content.upper()

            async def simple_on_response_tool_call(
                self, tool_call: AnthropicToolUseBlock, context: PolicyContext
            ) -> AnthropicToolUseBlock:
                return {
                    "type": "tool_use",
                    "id": tool_call["id"],
                    "name": f"test_{tool_call['name']}",
                    "input": tool_call["input"],
                }

        policy = MixedPolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "let me check"}
        tool_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_456",
            "name": "search",
            "input": {"query": "test"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block, tool_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 15},
        }

        result = await policy.on_response(response, ctx)

        result_text = cast(AnthropicTextBlock, result["content"][0])
        result_tool = cast(AnthropicToolUseBlock, result["content"][1])
        assert result_text["text"] == "LET ME CHECK"
        assert result_tool["name"] == "test_search"

    @pytest.mark.asyncio
    async def test_on_response_preserves_metadata(self):
        """on_response preserves response metadata like usage and stop_reason."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "test"}
        response: AnthropicResponse = {
            "id": "msg_789",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 25, "output_tokens": 15},
        }

        result = await policy.on_response(response, ctx)

        assert result["id"] == "msg_789"
        assert result["model"] == "claude-sonnet-4-20250514"
        assert result.get("stop_reason") == "end_turn"
        assert result["usage"]["input_tokens"] == 25
        assert result["usage"]["output_tokens"] == 15


class TestAnthropicSimplePolicyStreamEventBasic:
    """Tests for basic on_stream_event behavior."""

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_start(self):
        """on_stream_event passes through message_start events unchanged."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageStartEvent.model_construct(
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

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_delta(self):
        """on_stream_event passes through message_delta events unchanged."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 10},
        )

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_stop(self):
        """on_stream_event passes through message_stop events unchanged."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageStopEvent.model_construct(type="message_stop")

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_thinking_delta(self):
        """on_stream_event passes through thinking_delta events unchanged."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        thinking_delta = ThinkingDelta.model_construct(type="thinking_delta", thinking="Let me think...")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=thinking_delta,
        )

        result = await policy.on_stream_event(event, ctx)

        assert result is event


class TestAnthropicSimplePolicyStreamEventText:
    """Tests for streaming text content transformation."""

    @pytest.mark.asyncio
    async def test_on_stream_event_buffers_text_deltas(self):
        """on_stream_event buffers text_delta events and returns None."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start text block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_stream_event(start_event, ctx)

        # Send text delta - should be buffered and return None
        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy.on_stream_event(delta_event, ctx)

        assert result is None

    @pytest.mark.asyncio
    async def test_on_stream_event_emits_transformed_text_on_stop(self):
        """on_stream_event emits transformed text when content_block_stop is received."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start text block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_stream_event(start_event, ctx)

        # Send text deltas
        for text in ["hello", " ", "world"]:
            delta = TextDelta.model_construct(type="text_delta", text=text)
            delta_event = RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=delta,
            )
            await policy.on_stream_event(delta_event, ctx)

        # Stop block - should emit transformed content
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        result = await policy.on_stream_event(stop_event, ctx)

        # Should emit a delta event with transformed text
        assert result is not None
        assert isinstance(result, RawContentBlockDeltaEvent)
        assert isinstance(result.delta, TextDelta)
        assert result.delta.text == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_stream_event_has_pending_stop_after_transform(self):
        """After emitting transformed text, pending stop event is available."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start text block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_stream_event(start_event, ctx)

        # Send text delta
        delta = TextDelta.model_construct(type="text_delta", text="hello")
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=delta,
        )
        await policy.on_stream_event(delta_event, ctx)

        # Stop block
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )
        await policy.on_stream_event(stop_event, ctx)

        # Check pending stop event
        pending = policy.get_pending_stop_event()
        assert pending is not None
        assert pending.type == "content_block_stop"


class TestAnthropicSimplePolicyStreamEventToolUse:
    """Tests for streaming tool_use content transformation."""

    @pytest.mark.asyncio
    async def test_on_stream_event_buffers_json_deltas(self):
        """on_stream_event buffers input_json_delta events and returns None."""
        policy = PrefixToolNamePolicy()
        ctx = PolicyContext.for_testing()

        # Start tool_use block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="get_weather",
                input={},
            ),
        )
        await policy.on_stream_event(start_event, ctx)

        # Send JSON delta - should be buffered and return None
        json_delta = InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"loc')
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=json_delta,
        )

        result = await policy.on_stream_event(delta_event, ctx)

        assert result is None

    @pytest.mark.asyncio
    async def test_on_stream_event_emits_transformed_tool_on_stop(self):
        """on_stream_event emits transformed tool call when content_block_stop is received."""
        policy = PrefixToolNamePolicy()
        ctx = PolicyContext.for_testing()

        # Start tool_use block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="get_weather",
                input={},
            ),
        )
        await policy.on_stream_event(start_event, ctx)

        # Send JSON deltas
        for json_part in ['{"location"', ': "NYC"}']:
            json_delta = InputJSONDelta.model_construct(type="input_json_delta", partial_json=json_part)
            delta_event = RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=json_delta,
            )
            await policy.on_stream_event(delta_event, ctx)

        # Stop block - should emit transformed content
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        result = await policy.on_stream_event(stop_event, ctx)

        # Should emit a delta event with transformed JSON
        assert result is not None
        assert isinstance(result, RawContentBlockDeltaEvent)
        assert isinstance(result.delta, InputJSONDelta)
        # The transformed input should contain the original data
        assert "NYC" in result.delta.partial_json


class TestAnthropicSimplePolicyBufferManagement:
    """Tests for buffer management."""

    def test_clear_buffers(self):
        """clear_buffers removes all buffered content."""
        policy = AnthropicSimplePolicy()
        policy._text_buffer[0] = "some text"
        policy._tool_buffer[1] = {"id": "test", "name": "tool", "input_json": "{}"}
        policy._pending_stop_event = RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0)

        policy.clear_buffers()

        assert policy._text_buffer == {}
        assert policy._tool_buffer == {}
        assert policy.get_pending_stop_event() is None

    @pytest.mark.asyncio
    async def test_multiple_content_blocks(self):
        """Policy handles multiple content blocks with separate buffers."""
        policy = UppercaseSimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start two text blocks
        for idx in [0, 1]:
            start_event = RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=idx,
                content_block=TextBlock.model_construct(type="text", text=""),
            )
            await policy.on_stream_event(start_event, ctx)

        # Send deltas to both blocks
        delta0 = TextDelta.model_construct(type="text_delta", text="first")
        delta_event0 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=delta0,
        )
        await policy.on_stream_event(delta_event0, ctx)

        delta1 = TextDelta.model_construct(type="text_delta", text="second")
        delta_event1 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=1,
            delta=delta1,
        )
        await policy.on_stream_event(delta_event1, ctx)

        # Stop first block
        stop_event0 = RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0)
        result0 = await policy.on_stream_event(stop_event0, ctx)

        # Clear pending for next check
        policy.get_pending_stop_event()

        # Stop second block
        stop_event1 = RawContentBlockStopEvent.model_construct(type="content_block_stop", index=1)
        result1 = await policy.on_stream_event(stop_event1, ctx)

        # Verify both were transformed independently
        result0_event = cast(RawContentBlockDeltaEvent, result0)
        result1_event = cast(RawContentBlockDeltaEvent, result1)
        assert result0_event.delta.text == "FIRST"
        assert result1_event.delta.text == "SECOND"


__all__ = [
    "TestAnthropicSimplePolicyProtocol",
    "TestAnthropicSimplePolicyRequest",
    "TestAnthropicSimplePolicyResponse",
    "TestAnthropicSimplePolicyStreamEventBasic",
    "TestAnthropicSimplePolicyStreamEventText",
    "TestAnthropicSimplePolicyStreamEventToolUse",
    "TestAnthropicSimplePolicyBufferManagement",
]
