# ABOUTME: Unit tests for SimplePolicy covering both OpenAI and Anthropic behavior
"""Unit tests for SimplePolicy block-based behavior.

Tests enforce that SimplePolicy:
1. Does NOT emit chunks during on_content_delta / on_tool_call_delta
2. DOES emit complete blocks during on_content_complete / on_tool_call_complete
3. Passes through metadata chunks immediately
4. Only transforms when transformation is needed
5. Supports both OpenAI and Anthropic API formats
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, Mock

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
from litellm.types.utils import ChatCompletionMessageToolCall, Function, ModelResponse

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core import AnthropicPolicyInterface, OpenAIPolicyInterface
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock
from luthien_proxy.streaming.stream_state import StreamState

if TYPE_CHECKING:
    pass


# ===== OpenAI Test Policy Subclasses =====


class NoTransformPolicy(SimplePolicy):
    """SimplePolicy subclass that doesn't transform anything."""

    pass


class UppercasePolicy(SimplePolicy):
    """SimplePolicy subclass that uppercases content."""

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Transform content to uppercase."""
        return content.upper()


class ToolBlockerPolicy(SimplePolicy):
    """SimplePolicy subclass that blocks tool calls."""

    async def simple_on_response_tool_call(
        self, tool_call: ChatCompletionMessageToolCall, context: PolicyContext
    ) -> ChatCompletionMessageToolCall:
        """Transform tool call to blocked function."""
        return ChatCompletionMessageToolCall(
            id=tool_call.id,
            type="function",
            function=Function(name="blocked", arguments="{}"),
        )


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


# ===== Test Fixtures =====


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


# ===== Protocol Implementation Tests =====


class TestSimplePolicyProtocol:
    """Tests verifying SimplePolicy implements the required protocols."""

    def test_implements_openai_interface(self):
        """SimplePolicy satisfies OpenAIPolicyInterface."""
        policy = SimplePolicy()
        assert isinstance(policy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self):
        """SimplePolicy satisfies AnthropicPolicyInterface."""
        policy = SimplePolicy()
        assert isinstance(policy, AnthropicPolicyInterface)

    def test_has_short_policy_name(self):
        """SimplePolicy has a short_policy_name property defaulting to class name."""
        policy = SimplePolicy()
        assert policy.short_policy_name == "SimplePolicy"

    def test_subclass_short_policy_name(self):
        """Subclass uses its own class name for short_policy_name."""
        policy = UppercasePolicy()
        assert policy.short_policy_name == "UppercasePolicy"


# ===== OpenAI Streaming Tests =====


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
    async def test_on_tool_call_complete_emits_only_one_chunk(self):
        """Regression test: on_tool_call_complete should emit exactly ONE chunk.

        Previously, SimplePolicy was emitting both the tool call chunk AND a separate
        finish_reason chunk, causing duplicate message_delta events in Anthropic SSE output.
        The tool call chunk already includes finish_reason="tool_calls", so no separate
        finish_reason chunk should be emitted.
        """
        policy = NoTransformPolicy()

        # Create a completed tool call block
        block = ToolCallStreamBlock(id="call-123", index=0, name="get_weather", arguments='{"location": "NYC"}')
        block.is_complete = True

        # Create raw chunks including one with finish_reason (simulating real stream)
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
                        "finish_reason": "tool_calls",  # finish_reason present
                    }
                ],
            ),
        ]

        ctx = create_mock_context(just_completed=block, raw_chunks=raw_chunks)

        # Call on_tool_call_complete
        await policy.on_tool_call_complete(ctx)

        # Verify exactly ONE chunk was emitted (the tool call chunk with embedded finish_reason)
        # NOT two chunks (tool call + separate finish_reason)
        assert ctx.egress_queue.put.call_count == 1

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


class TestSimplePolicyNonStreaming:
    """Test that SimplePolicy handles non-streaming responses correctly."""

    @pytest.fixture
    def mock_policy_context(self):
        """Create a mock PolicyContext for non-streaming tests."""
        ctx = Mock(spec=PolicyContext)
        ctx.transaction_id = "test-transaction-id"
        ctx.request = Request(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        )
        ctx.scratchpad = {}
        return ctx

    @pytest.mark.asyncio
    async def test_on_response_transforms_content(self, mock_policy_context):
        """Test that on_openai_response applies simple_on_response_content transformation."""
        from litellm.types.utils import Choices, Message

        policy = UppercasePolicy()

        # Create a non-streaming response
        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="hello world"),
                    finish_reason="stop",
                )
            ],
        )

        result = await policy.on_openai_response(response, mock_policy_context)

        # Verify content was transformed to uppercase
        assert result.choices[0].message.content == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_response_no_transform_passthrough(self, mock_policy_context):
        """Test that on_openai_response passes through when no transformation is defined."""
        from litellm.types.utils import Choices, Message

        policy = NoTransformPolicy()

        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="hello world"),
                    finish_reason="stop",
                )
            ],
        )

        result = await policy.on_openai_response(response, mock_policy_context)

        # Content should be unchanged
        assert result.choices[0].message.content == "hello world"

    @pytest.mark.asyncio
    async def test_on_response_empty_choices(self, mock_policy_context):
        """Test that on_openai_response handles empty choices gracefully."""
        policy = UppercasePolicy()

        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[],
        )

        result = await policy.on_openai_response(response, mock_policy_context)

        # Should return response unchanged
        assert result.choices == []

    @pytest.mark.asyncio
    async def test_on_response_non_string_content_skipped(self, mock_policy_context):
        """Test that on_openai_response skips choices with non-string content."""
        from litellm.types.utils import Choices, Message

        policy = UppercasePolicy()

        # Create response with None content (e.g., tool call response)
        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content=None),
                    finish_reason="tool_calls",
                )
            ],
        )

        result = await policy.on_openai_response(response, mock_policy_context)

        # Content should remain None (not transformed)
        assert result.choices[0].message.content is None

    @pytest.mark.asyncio
    async def test_on_response_transforms_tool_calls(self, mock_policy_context):
        """Test that on_openai_response applies simple_on_response_tool_call transformation."""
        from litellm.types.utils import Choices, Message

        policy = ToolBlockerPolicy()

        # Create a non-streaming response with tool calls
        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(
                        role="assistant",
                        content=None,
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call-123",
                                type="function",
                                function=Function(name="dangerous_function", arguments='{"do_bad": true}'),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
        )

        result = await policy.on_openai_response(response, mock_policy_context)

        # Tool call should be transformed to "blocked"
        assert result.choices[0].message.tool_calls is not None
        assert len(result.choices[0].message.tool_calls) == 1
        assert result.choices[0].message.tool_calls[0].function.name == "blocked"
        assert result.choices[0].message.tool_calls[0].function.arguments == "{}"


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

        content_list = cast(list, result["messages"][-1]["content"])
        text_block_result = cast(AnthropicTextBlock, content_list[0])
        assert text_block_result["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_request_empty_messages(self):
        """on_anthropic_request handles empty messages list gracefully."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result["messages"] == []


# ===== Anthropic Response Tests =====


class TestSimplePolicyAnthropicResponse:
    """Tests for Anthropic on_anthropic_response behavior."""

    @pytest.mark.asyncio
    async def test_on_response_passthrough_by_default(self):
        """Base class on_anthropic_response passes through content unchanged."""
        policy = SimplePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello world"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_on_response_transforms_text_content(self):
        """Subclass simple_on_response_content transforms text blocks."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "hello world"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_on_response_transforms_multiple_text_blocks(self):
        """Subclass transforms all text blocks in response."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        text_block1: AnthropicTextBlock = {"type": "text", "text": "first block"}
        text_block2: AnthropicTextBlock = {"type": "text", "text": "second block"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block1, text_block2],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_block0 = cast(AnthropicTextBlock, result["content"][0])
        result_block1 = cast(AnthropicTextBlock, result["content"][1])
        assert result_block0["text"] == "FIRST BLOCK"
        assert result_block1["text"] == "SECOND BLOCK"

    @pytest.mark.asyncio
    async def test_on_response_transforms_tool_calls(self):
        """Subclass simple_on_anthropic_tool_call transforms tool_use blocks."""
        policy = AnthropicPrefixToolNamePolicy()
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
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_tool_block = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool_block["name"] == "test_get_weather"
        assert result_tool_block["input"] == {"location": "NYC"}

    @pytest.mark.asyncio
    async def test_on_response_mixed_content(self):
        """Subclass transforms both text and tool blocks in mixed content."""

        # Create a policy that does both transformations
        class MixedPolicy(SimplePolicy):
            async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
                return content.upper()

            async def simple_on_anthropic_tool_call(
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
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 15},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text = cast(AnthropicTextBlock, result["content"][0])
        result_tool = cast(AnthropicToolUseBlock, result["content"][1])
        assert result_text["text"] == "LET ME CHECK"
        assert result_tool["name"] == "test_search"

    @pytest.mark.asyncio
    async def test_on_response_preserves_metadata(self):
        """on_anthropic_response preserves response metadata like usage and stop_reason."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "test"}
        response: AnthropicResponse = {
            "id": "msg_789",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 25, "output_tokens": 15},
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result["id"] == "msg_789"
        assert result["model"] == DEFAULT_TEST_MODEL
        assert result.get("stop_reason") == "end_turn"
        assert result["usage"]["input_tokens"] == 25
        assert result["usage"]["output_tokens"] == 15


# ===== Anthropic Streaming Event Tests =====


class TestSimplePolicyAnthropicStreamEventBasic:
    """Tests for basic on_anthropic_stream_event behavior."""

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_start(self):
        """on_anthropic_stream_event passes through message_start events unchanged."""
        policy = AnthropicUppercasePolicy()
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
    async def test_on_stream_event_passes_through_message_delta(self):
        """on_anthropic_stream_event passes through message_delta events unchanged."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 10},
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_stop(self):
        """on_anthropic_stream_event passes through message_stop events unchanged."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageStopEvent.model_construct(type="message_stop")

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_thinking_delta(self):
        """on_anthropic_stream_event passes through thinking_delta events unchanged."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        thinking_delta = ThinkingDelta.model_construct(type="thinking_delta", thinking="Let me think...")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=thinking_delta,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]


class TestSimplePolicyAnthropicStreamEventText:
    """Tests for Anthropic streaming text content transformation."""

    @pytest.mark.asyncio
    async def test_on_stream_event_buffers_text_deltas(self):
        """on_anthropic_stream_event buffers text_delta events and returns None."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        # Start text block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Send text delta - should be buffered and return empty list
        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy.on_anthropic_stream_event(delta_event, ctx)

        assert result == []

    @pytest.mark.asyncio
    async def test_on_stream_event_emits_transformed_text_on_stop(self):
        """on_anthropic_stream_event emits transformed text when content_block_stop is received."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        # Start text block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Send text deltas
        for text in ["hello", " ", "world"]:
            delta = TextDelta.model_construct(type="text_delta", text=text)
            delta_event = RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=delta,
            )
            await policy.on_anthropic_stream_event(delta_event, ctx)

        # Stop block - should emit transformed content
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        result = await policy.on_anthropic_stream_event(stop_event, ctx)

        # Should emit a delta event followed by the stop event
        assert len(result) == 2
        assert isinstance(result[0], RawContentBlockDeltaEvent)
        assert isinstance(result[0].delta, TextDelta)
        assert result[0].delta.text == "HELLO WORLD"
        assert isinstance(result[1], RawContentBlockStopEvent)
        assert result[1].type == "content_block_stop"


class TestSimplePolicyAnthropicStreamEventToolUse:
    """Tests for Anthropic streaming tool_use content transformation."""

    @pytest.mark.asyncio
    async def test_on_stream_event_buffers_json_deltas(self):
        """on_anthropic_stream_event buffers input_json_delta events and returns None."""
        policy = AnthropicPrefixToolNamePolicy()
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
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Send JSON delta - should be buffered and return empty list
        json_delta = InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"loc')
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=json_delta,
        )

        result = await policy.on_anthropic_stream_event(delta_event, ctx)

        assert result == []

    @pytest.mark.asyncio
    async def test_on_stream_event_emits_transformed_tool_on_stop(self):
        """on_anthropic_stream_event emits transformed tool call when content_block_stop is received."""
        policy = AnthropicPrefixToolNamePolicy()
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
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Send JSON deltas
        for json_part in ['{"location"', ': "NYC"}']:
            json_delta = InputJSONDelta.model_construct(type="input_json_delta", partial_json=json_part)
            delta_event = RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=json_delta,
            )
            await policy.on_anthropic_stream_event(delta_event, ctx)

        # Stop block - should emit transformed content
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        result = await policy.on_anthropic_stream_event(stop_event, ctx)

        # Should emit a delta event followed by the stop event
        assert len(result) == 2
        assert isinstance(result[0], RawContentBlockDeltaEvent)
        assert isinstance(result[0].delta, InputJSONDelta)
        # The transformed input should contain the original data
        assert "NYC" in result[0].delta.partial_json
        assert isinstance(result[1], RawContentBlockStopEvent)


# ===== Anthropic Buffer Management Tests =====


class TestSimplePolicyAnthropicBufferManagement:
    """Tests for Anthropic buffer management."""

    @pytest.mark.asyncio
    async def test_multiple_content_blocks(self):
        """Policy handles multiple content blocks with separate buffers."""
        policy = AnthropicUppercasePolicy()
        ctx = PolicyContext.for_testing()

        # Start two text blocks
        for idx in [0, 1]:
            start_event = RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=idx,
                content_block=TextBlock.model_construct(type="text", text=""),
            )
            await policy.on_anthropic_stream_event(start_event, ctx)

        # Send deltas to both blocks
        delta0 = TextDelta.model_construct(type="text_delta", text="first")
        delta_event0 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=delta0,
        )
        await policy.on_anthropic_stream_event(delta_event0, ctx)

        delta1 = TextDelta.model_construct(type="text_delta", text="second")
        delta_event1 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=1,
            delta=delta1,
        )
        await policy.on_anthropic_stream_event(delta_event1, ctx)

        # Stop first block
        stop_event0 = RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0)
        result0 = await policy.on_anthropic_stream_event(stop_event0, ctx)

        # Stop second block
        stop_event1 = RawContentBlockStopEvent.model_construct(type="content_block_stop", index=1)
        result1 = await policy.on_anthropic_stream_event(stop_event1, ctx)

        # Verify both were transformed independently (each returns [delta, stop])
        assert len(result0) == 2
        assert len(result1) == 2
        result0_event = cast(RawContentBlockDeltaEvent, result0[0])
        result1_event = cast(RawContentBlockDeltaEvent, result1[0])
        assert result0_event.delta.text == "FIRST"
        assert result1_event.delta.text == "SECOND"


# ===== Error Handling Tests =====


class TestSimplePolicyErrorHandling:
    """Tests that SimplePolicy raises errors instead of silently suppressing them."""

    @pytest.fixture
    def mock_policy_context(self):
        """Create a mock PolicyContext for non-streaming tests."""
        ctx = Mock(spec=PolicyContext)
        ctx.transaction_id = "test-transaction-id"
        ctx.request = Request(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        )
        ctx.scratchpad = {}
        return ctx

    @pytest.mark.asyncio
    async def test_on_openai_response_raises_on_non_choices_type(self, mock_policy_context):
        """on_openai_response raises TypeError when choice is not Choices type."""
        policy = SimplePolicy()

        # Create response then manually set a non-Choices object
        # (ModelResponse auto-converts dicts to Choices, so we bypass that)
        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[],
        )
        response.choices = ["not a Choices object"]  # type: ignore[list-item]

        with pytest.raises(TypeError) as exc_info:
            await policy.on_openai_response(response, mock_policy_context)

        assert "Expected choice to be Choices" in str(exc_info.value)
        assert "unexpected response format" in str(exc_info.value)

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
