"""Unit tests for SimplePolicy block-based behavior.

Tests enforce that SimplePolicy:
1. Does NOT emit chunks during on_content_delta / on_tool_call_delta
2. DOES emit complete blocks during on_content_complete / on_tool_call_complete
3. Passes through metadata chunks immediately
4. Only transforms when transformation is needed
5. Supports Anthropic API format
"""

from __future__ import annotations

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

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core import AnthropicExecutionInterface

# ===== Anthropic Test Policy Subclasses =====


class AnthropicUppercasePolicy(SimplePolicy):
    """Test policy that transforms text to uppercase (Anthropic variant)."""

    async def simple_on_request(self, request_text: str, context: PolicyContext) -> str:
        return request_text.upper()

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        return content.upper()


class AnthropicPrefixToolNamePolicy(SimplePolicy):
    """Test policy that prefixes tool names with 'test_'."""

    async def simple_on_anthropic_tool_call(
        self, tool_call: AnthropicToolUseBlock, context: PolicyContext
    ) -> AnthropicToolUseBlock:
        return {
            "type": "tool_use",
            "id": tool_call["id"],
            "name": f"test_{tool_call['name']}",
            "input": tool_call["input"],
        }


# ===== Protocol Implementation Tests =====


class TestSimplePolicyProtocol:
    """Tests verifying SimplePolicy implements the required protocols."""

    def test_implements_anthropic_interface(self):
        """SimplePolicy satisfies AnthropicExecutionInterface."""
        policy = SimplePolicy()
        assert isinstance(policy, AnthropicExecutionInterface)

    def test_has_short_policy_name(self):
        """SimplePolicy has a short_policy_name property defaulting to class name."""
        policy = SimplePolicy()
        assert policy.short_policy_name == "SimplePolicy"

    def test_subclass_short_policy_name(self):
        """Subclass uses its own class name for short_policy_name."""
        policy = AnthropicUppercasePolicy()
        assert policy.short_policy_name == "AnthropicUppercasePolicy"


# ===== Anthropic Request Tests =====


class TestSimplePolicyAnthropicRequest:
    """Tests for Anthropic on_anthropic_request behavior."""

    @pytest.mark.asyncio
    async def test_on_request_passthrough_by_default(self):
        """Base class on_anthropic_request passes through text unchanged."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello world"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result["messages"][-1]["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_on_request_transforms_string_content(self):
        """Subclass simple_on_request transforms string message content."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "hello world"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result["messages"][-1]["content"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_request_transforms_text_block_content(self):
        """Subclass simple_on_request transforms text blocks in message content list."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "hello world"}
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": [text_block]}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        result_content = result["messages"][-1]["content"]
        assert isinstance(result_content, list)
        result_text_block = cast(AnthropicTextBlock, result_content[0])
        assert result_text_block["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_request_ignores_tool_use_blocks(self):
        """on_anthropic_request does not transform tool_use blocks in messages."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        tool_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "test_tool",
            "input": {"key": "value"},
        }
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": [tool_block]}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        result_content = result["messages"][-1]["content"]
        assert isinstance(result_content, list)
        result_tool_block = cast(AnthropicToolUseBlock, result_content[0])
        assert result_tool_block["name"] == "test_tool"


# ===== Anthropic Response Tests =====


class TestSimplePolicyAnthropicResponse:
    """Tests for Anthropic on_anthropic_response behavior."""

    @pytest.mark.asyncio
    async def test_on_response_passthrough_by_default(self):
        """Base class on_anthropic_response passes through response unchanged."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello world"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result is response
        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_on_response_transforms_text_content(self):
        """Subclass simple_on_response_content transforms text blocks."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello world"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_response_transforms_multiple_text_blocks(self):
        """on_anthropic_response transforms all text blocks in content."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
            ],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        block_0 = cast(AnthropicTextBlock, result["content"][0])
        block_1 = cast(AnthropicTextBlock, result["content"][1])
        assert block_0["text"] == "HELLO"
        assert block_1["text"] == "WORLD"

    @pytest.mark.asyncio
    async def test_on_response_transforms_tool_use_blocks(self):
        """Subclass simple_on_anthropic_tool_call transforms tool_use blocks."""
        policy = AnthropicPrefixToolNamePolicy()
        ctx = PolicyContext.for_testing()

        tool_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "execute",
            "input": {"command": "ls"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_tool_block = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool_block["name"] == "test_execute"

    @pytest.mark.asyncio
    async def test_on_response_mixed_content_blocks(self):
        """on_anthropic_response transforms text and tool_use blocks correctly."""
        policy = AnthropicPrefixToolNamePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "calling tool"}
        tool_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_456",
            "name": "search",
            "input": {"query": "example"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block, tool_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 15},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        result_tool_block = cast(AnthropicToolUseBlock, result["content"][1])
        assert result_text_block["text"] == "calling tool"
        assert result_tool_block["name"] == "test_search"


# ===== Anthropic Streaming Event Tests =====


class TestSimplePolicyAnthropicStreamEventBasic:
    """Tests for basic streaming event handling."""

    @pytest.mark.asyncio
    async def test_message_start_passes_through(self):
        """on_anthropic_stream_event passes message_start unchanged."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageStartEvent.model_construct(
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

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_content_block_start_initializes_buffer(self):
        """on_anthropic_stream_event initializes text buffer for content_block_start."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]
        # Verify buffer was initialized
        assert 0 in policy._anthropic_state(ctx).text_buffer

    @pytest.mark.asyncio
    async def test_content_block_stop_passes_through(self):
        """on_anthropic_stream_event passes through content_block_stop event."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Add delta
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="hello"),
        )
        await policy.on_anthropic_stream_event(delta_event, ctx)

        # Stop block
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )
        result = await policy.on_anthropic_stream_event(stop_event, ctx)

        # Stop event should be passed through
        assert stop_event in result

    @pytest.mark.asyncio
    async def test_message_delta_passes_through(self):
        """on_anthropic_stream_event passes message_delta unchanged."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 10},
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_message_stop_passes_through(self):
        """on_anthropic_stream_event passes message_stop unchanged."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageStopEvent.model_construct(type="message_stop")

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]


class TestSimplePolicyAnthropicStreamEventText:
    """Tests for text content streaming."""

    @pytest.mark.asyncio
    async def test_text_delta_buffers_content(self):
        """on_anthropic_stream_event buffers TextDelta without emitting."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Delta
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="hello"),
        )
        result = await policy.on_anthropic_stream_event(delta_event, ctx)

        # Buffer the delta, don't emit it
        assert result == []
        assert policy._anthropic_state(ctx).text_buffer[0] == "hello"

    @pytest.mark.asyncio
    async def test_text_delta_accumulates(self):
        """Multiple TextDeltas accumulate in the buffer."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Multiple deltas
        delta_1 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="hello "),
        )
        delta_2 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=TextDelta.model_construct(type="text_delta", text="world"),
        )

        await policy.on_anthropic_stream_event(delta_1, ctx)
        await policy.on_anthropic_stream_event(delta_2, ctx)

        assert policy._anthropic_state(ctx).text_buffer[0] == "hello world"

    @pytest.mark.asyncio
    async def test_thinking_delta_passes_through(self):
        """ThinkingDelta passes through unchanged (not buffered like TextDelta)."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block={"type": "thinking", "thinking": ""},
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Delta
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=ThinkingDelta.model_construct(type="thinking_delta", thinking="consider this"),
        )
        result = await policy.on_anthropic_stream_event(delta_event, ctx)

        # Thinking delta passes through
        assert len(result) == 1
        assert result[0] is delta_event


class TestSimplePolicyAnthropicStreamEventToolUse:
    """Tests for tool_use streaming."""

    @pytest.mark.asyncio
    async def test_tool_use_start_initializes_buffer(self):
        """on_anthropic_stream_event initializes tool buffer for tool_use block."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="execute",
                input={},
            ),
        )

        await policy.on_anthropic_stream_event(event, ctx)

        assert 0 in policy._anthropic_state(ctx).tool_buffer

    @pytest.mark.asyncio
    async def test_json_delta_buffers_incrementally(self):
        """InputJSONDelta accumulates in tool buffer."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start tool_use
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_456",
                name="search",
                input={},
            ),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # JSON deltas
        delta_1 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"key": "'),
        )
        delta_2 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='value"}'),
        )

        await policy.on_anthropic_stream_event(delta_1, ctx)
        await policy.on_anthropic_stream_event(delta_2, ctx)

        assert policy._anthropic_state(ctx).tool_buffer[0].input_json == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_tool_use_stop_completes_and_transforms(self):
        """on_anthropic_stream_event completes tool_use and calls transform."""
        policy = AnthropicPrefixToolNamePolicy()
        ctx = PolicyContext.for_testing()

        # Start
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_789",
                name="delete",
                input={},
            ),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # JSON delta
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json="{}"),
        )
        await policy.on_anthropic_stream_event(delta_event, ctx)

        # Stop
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )
        result = await policy.on_anthropic_stream_event(stop_event, ctx)

        # Result should contain both the transformed tool block and the stop event
        assert len(result) == 2
        assert result[-1] is stop_event


# ===== Anthropic Buffer Management Tests =====


class TestSimplePolicyAnthropicBufferManagement:
    """Tests for per-request state isolation."""

    @pytest.mark.asyncio
    async def test_separate_contexts_have_separate_buffers(self):
        """Different PolicyContexts maintain independent buffers."""
        policy = SimplePolicy()
        ctx_a = PolicyContext.for_testing(transaction_id="txn-a")
        ctx_b = PolicyContext.for_testing(transaction_id="txn-b")

        # Start block in ctx_a
        start_a = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_anthropic_stream_event(start_a, ctx_a)

        # Verify ctx_a has buffer
        assert 0 in policy._anthropic_state(ctx_a).text_buffer
        # Verify ctx_b does not
        assert 0 not in policy._anthropic_state(ctx_b).text_buffer

    @pytest.mark.asyncio
    async def test_on_anthropic_streaming_policy_complete_cleans_state(self):
        """on_anthropic_streaming_policy_complete removes per-request state."""
        policy = SimplePolicy()
        ctx_a = PolicyContext.for_testing(transaction_id="txn-a")
        ctx_b = PolicyContext.for_testing(transaction_id="txn-b")

        start_a = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        start_b = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_anthropic_stream_event(start_a, ctx_a)
        await policy.on_anthropic_stream_event(start_b, ctx_b)

        await policy.on_anthropic_streaming_policy_complete(ctx_a)

        assert policy._anthropic_state(ctx_a).text_buffer == {}
        assert 0 in policy._anthropic_state(ctx_b).text_buffer


# ===== Error Handling Tests =====


class TestSimplePolicyErrorHandling:
    """Tests that SimplePolicy raises errors instead of silently suppressing them."""

    @pytest.mark.asyncio
    async def test_on_stream_event_raises_on_text_delta_without_buffer(self):
        """on_anthropic_stream_event raises RuntimeError when TextDelta received without buffer."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        # Send text delta WITHOUT starting a block first
        text_delta = TextDelta.model_construct(type="text_delta", text="orphan delta")
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        with pytest.raises(RuntimeError) as exc_info:
            await policy.on_anthropic_stream_event(delta_event, ctx)

        assert "Received TextDelta for index 0" in str(exc_info.value)
        assert "no buffer exists" in str(exc_info.value)
        assert "missing content_block_start" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_on_stream_event_raises_on_json_delta_without_buffer(self):
        """on_anthropic_stream_event raises RuntimeError when InputJSONDelta received without buffer."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        # Send JSON delta WITHOUT starting a tool block first
        json_delta = InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"key":')
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=json_delta,
        )

        with pytest.raises(RuntimeError) as exc_info:
            await policy.on_anthropic_stream_event(delta_event, ctx)

        assert "Received InputJSONDelta for index 0" in str(exc_info.value)
        assert "no buffer exists" in str(exc_info.value)
        assert "missing content_block_start" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_on_stream_event_raises_on_malformed_json(self):
        """on_anthropic_stream_event raises JSONDecodeError for malformed tool call JSON."""
        import json as json_module

        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        # Start tool_use block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="test_tool",
                input={},
            ),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Send malformed JSON delta
        json_delta = InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"key": invalid}')
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=json_delta,
        )
        await policy.on_anthropic_stream_event(delta_event, ctx)

        # Stop block - should raise JSONDecodeError when parsing
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        with pytest.raises(json_module.JSONDecodeError):
            await policy.on_anthropic_stream_event(stop_event, ctx)

    @pytest.mark.asyncio
    async def test_on_anthropic_response_raises_on_missing_tool_use_id(self):
        """on_anthropic_response raises ValueError when tool_use block is missing id."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "test_tool",
                    "input": {},
                }  # Missing "id" field
            ],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with pytest.raises(ValueError) as exc_info:
            await policy.on_anthropic_response(response, ctx)

        assert "Malformed tool_use block" in str(exc_info.value)
        assert "id=None" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_on_anthropic_response_raises_on_missing_tool_use_name(self):
        """on_anthropic_response raises ValueError when tool_use block is missing name."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_123",
                    "input": {},
                }  # Missing "name" field
            ],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with pytest.raises(ValueError) as exc_info:
            await policy.on_anthropic_response(response, ctx)

        assert "Malformed tool_use block" in str(exc_info.value)
        assert "name=None" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
