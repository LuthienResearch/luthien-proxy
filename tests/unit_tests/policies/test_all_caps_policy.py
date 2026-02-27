"""Unit tests for AllCapsPolicy.

Tests both OpenAI and Anthropic interface implementations:
- OpenAI: non-streaming response, streaming content/tool deltas
- Anthropic: non-streaming response, streaming events
"""

import asyncio
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
    TextDelta,
    ThinkingDelta,
)
from litellm.types.utils import Choices, Message, ModelResponse
from tests.unit_tests.helpers.litellm_test_utils import make_streaming_chunk

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies.all_caps_policy import AllCapsPolicy
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_state import StreamState


@pytest.fixture
def policy():
    """Create an AllCapsPolicy instance."""
    return AllCapsPolicy()


@pytest.fixture
def policy_context():
    """Create a basic policy context."""
    return PolicyContext(
        transaction_id="test-txn-123",
        request=Request(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        ),
    )


@pytest.fixture
def streaming_context():
    """Create a streaming policy context."""
    stream_state = StreamState()
    policy_ctx = PolicyContext(
        transaction_id="test-txn-123",
        request=Request(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        ),
    )
    egress_queue = asyncio.Queue()
    return StreamingPolicyContext(
        policy_ctx=policy_ctx,
        egress_queue=egress_queue,
        original_streaming_response_state=stream_state,
        keepalive=lambda: None,
    )


# =============================================================================
# Protocol Compliance Tests
# =============================================================================


class TestAllCapsPolicyProtocol:
    """Tests verifying AllCapsPolicy implements the required interfaces."""

    def test_inherits_from_base_policy(self, policy):
        """AllCapsPolicy inherits from BasePolicy."""
        assert isinstance(policy, BasePolicy)

    def test_implements_openai_interface(self, policy):
        """AllCapsPolicy implements OpenAIPolicyInterface."""
        assert isinstance(policy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self, policy):
        """AllCapsPolicy implements AnthropicPolicyInterface."""
        assert isinstance(policy, AnthropicPolicyInterface)

    def test_policy_name(self, policy):
        """Test that policy has a readable name."""
        assert policy.short_policy_name == "AllCapsPolicy"


# =============================================================================
# OpenAI Non-Streaming Tests
# =============================================================================


class TestAllCapsPolicyOpenAINonStreaming:
    """Test OpenAI non-streaming response handling."""

    async def test_uppercase_text_response(self, policy, policy_context):
        """Test that text content is converted to uppercase."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content="hello world",
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_openai_response(response, policy_context)

        assert result.choices[0].message.content == "HELLO WORLD"

    async def test_uppercase_multiple_choices(self, policy, policy_context):
        """Test that all choices are converted to uppercase."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content="hello world",
                        role="assistant",
                    ),
                ),
                Choices(
                    finish_reason="stop",
                    index=1,
                    message=Message(
                        content="goodbye world",
                        role="assistant",
                    ),
                ),
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_openai_response(response, policy_context)

        assert result.choices[0].message.content == "HELLO WORLD"
        assert result.choices[1].message.content == "GOODBYE WORLD"

    async def test_already_uppercase_unchanged(self, policy, policy_context):
        """Test that already uppercase content is unchanged."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content="HELLO WORLD",
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_openai_response(response, policy_context)

        assert result.choices[0].message.content == "HELLO WORLD"

    async def test_empty_content(self, policy, policy_context):
        """Test that empty content is handled gracefully."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content=None,
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_openai_response(response, policy_context)

        assert result.choices[0].message.content is None

    async def test_no_choices(self, policy, policy_context):
        """Test that response with no choices is unchanged."""
        response = ModelResponse(
            id="test-id",
            choices=[],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_openai_response(response, policy_context)

        assert len(result.choices) == 0

    async def test_special_characters_preserved(self, policy, policy_context):
        """Test that special characters and formatting are preserved."""
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(
                        content="Hello, world!\n\tNew line here: 123 + 456 = 579",
                        role="assistant",
                    ),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_openai_response(response, policy_context)

        assert result.choices[0].message.content == "HELLO, WORLD!\n\tNEW LINE HERE: 123 + 456 = 579"


# =============================================================================
# OpenAI Streaming Tests
# =============================================================================


class TestAllCapsPolicyOpenAIStreaming:
    """Test OpenAI streaming response handling."""

    async def test_uppercase_content_delta(self, policy, streaming_context):
        """Test that content delta is converted to uppercase."""
        chunk = make_streaming_chunk(content="hello world", model="test-model", id="test-id", finish_reason=None)
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_content_delta(streaming_context)

        assert chunk.choices[0].delta.content == "HELLO WORLD"
        assert not streaming_context.egress_queue.empty()
        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk

    async def test_empty_content_delta(self, policy, streaming_context):
        """Test that empty content delta is handled gracefully."""
        chunk = make_streaming_chunk(content=None, model="test-model", id="test-id", finish_reason=None)
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_content_delta(streaming_context)

        assert not streaming_context.egress_queue.empty()
        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk

    async def test_tool_call_delta_unchanged(self, policy, streaming_context):
        """Test that tool call deltas are passed through unchanged."""
        chunk = make_streaming_chunk(
            content=None,
            model="test-model",
            id="test-id",
            finish_reason=None,
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_123",
                    "function": {"name": "get_weather", "arguments": '{"location": "'},
                    "type": "function",
                }
            ],
        )
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_tool_call_delta(streaming_context)

        assert not streaming_context.egress_queue.empty()
        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk
        assert chunk.choices[0].delta.tool_calls[0]["function"]["name"] == "get_weather"

    async def test_content_complete_hook(self, policy, streaming_context):
        """Test that on_content_complete hook exists and doesn't break."""
        await policy.on_content_complete(streaming_context)

    async def test_finish_reason_hook(self, policy, streaming_context):
        """Test that on_finish_reason hook exists and doesn't break."""
        await policy.on_finish_reason(streaming_context)

    async def test_stream_complete_hook(self, policy, streaming_context):
        """Test that on_stream_complete hook exists and doesn't break."""
        await policy.on_stream_complete(streaming_context)

    async def test_multiple_content_deltas(self, policy, streaming_context):
        """Test processing multiple content delta chunks in sequence."""
        chunks_and_expected = [
            ("Hello ", "HELLO "),
            ("world", "WORLD"),
            ("!", "!"),
        ]

        for original, expected in chunks_and_expected:
            chunk = make_streaming_chunk(content=original, model="test-model", id="test-id", finish_reason=None)
            streaming_context.original_streaming_response_state.raw_chunks.append(chunk)
            await policy.on_content_delta(streaming_context)
            assert chunk.choices[0].delta.content == expected

    async def test_mixed_case_content(self, policy, streaming_context):
        """Test various mixed case scenarios."""
        test_cases = [
            ("HeLLo WoRLd", "HELLO WORLD"),
            ("123 abc 456", "123 ABC 456"),
            ("test@example.com", "TEST@EXAMPLE.COM"),
            ("CamelCaseText", "CAMELCASETEXT"),
        ]

        for original, expected in test_cases:
            chunk = make_streaming_chunk(content=original, model="test-model", id="test-id", finish_reason=None)
            streaming_context.original_streaming_response_state.raw_chunks.append(chunk)
            await policy.on_content_delta(streaming_context)
            assert chunk.choices[0].delta.content == expected


# =============================================================================
# OpenAI Request Tests
# =============================================================================


class TestAllCapsPolicyOpenAIRequest:
    """Test OpenAI request handling."""

    async def test_request_unchanged(self, policy, policy_context):
        """Test that requests are passed through unchanged."""
        request = Request(
            model="test-model",
            messages=[
                {"role": "user", "content": "hello world"},
                {"role": "assistant", "content": "goodbye world"},
            ],
            max_tokens=100,
            temperature=0.7,
        )

        result = await policy.on_openai_request(request, policy_context)

        assert result == request
        assert result.messages[0]["content"] == "hello world"
        assert result.messages[1]["content"] == "goodbye world"


# =============================================================================
# Anthropic Request Tests
# =============================================================================


class TestAllCapsPolicyAnthropicRequest:
    """Tests for Anthropic on_anthropic_request passthrough behavior."""

    @pytest.mark.asyncio
    async def test_request_returns_same_request(self):
        """on_anthropic_request returns the exact same request object unchanged."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_request_preserves_all_fields(self):
        """on_anthropic_request preserves all fields in a complex request."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
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

        assert result["model"] == DEFAULT_TEST_MODEL
        assert len(result["messages"]) == 3
        assert result["max_tokens"] == 500
        assert result.get("temperature") == 0.7
        assert result.get("system") == "You are a helpful assistant."


# =============================================================================
# Anthropic Non-Streaming Response Tests
# =============================================================================


class TestAllCapsPolicyAnthropicResponse:
    """Tests for Anthropic on_anthropic_response text transformation behavior."""

    @pytest.mark.asyncio
    async def test_transforms_text_to_uppercase(self):
        """on_anthropic_response converts text content blocks to uppercase."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello, world!"}
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
        assert result_text_block["text"] == "HELLO, WORLD!"

    @pytest.mark.asyncio
    async def test_transforms_multiple_text_blocks(self):
        """on_anthropic_response transforms all text blocks to uppercase."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        text_block1: AnthropicTextBlock = {"type": "text", "text": "First block"}
        text_block2: AnthropicTextBlock = {"type": "text", "text": "Second block"}
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

        result_text_block0 = cast(AnthropicTextBlock, result["content"][0])
        result_text_block1 = cast(AnthropicTextBlock, result["content"][1])
        assert result_text_block0["text"] == "FIRST BLOCK"
        assert result_text_block1["text"] == "SECOND BLOCK"

    @pytest.mark.asyncio
    async def test_leaves_tool_use_unchanged(self):
        """on_anthropic_response does not modify tool_use content blocks."""
        policy = AllCapsPolicy()
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
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_tool_block = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"
        assert result_tool_block["input"] == {"location": "San Francisco"}

    @pytest.mark.asyncio
    async def test_mixed_content_blocks(self):
        """on_anthropic_response transforms text but leaves tool_use unchanged in mixed content."""
        policy = AllCapsPolicy()
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
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 15},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        result_tool_block = cast(AnthropicToolUseBlock, result["content"][1])
        assert result_text_block["text"] == "LET ME CHECK THE WEATHER"
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_preserves_usage_and_metadata(self):
        """on_anthropic_response preserves usage stats and other metadata."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Test"}
        response: AnthropicResponse = {
            "id": "msg_789",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 25, "output_tokens": 15},
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result["id"] == "msg_789"
        assert result["model"] == DEFAULT_TEST_MODEL
        assert result.get("stop_reason") == "end_turn"
        assert result["usage"]["input_tokens"] == 25
        assert result["usage"]["output_tokens"] == 15


# =============================================================================
# Anthropic Streaming Tests
# =============================================================================


class TestAllCapsPolicyAnthropicStreamEvent:
    """Tests for Anthropic on_anthropic_stream_event text delta transformation behavior."""

    @pytest.mark.asyncio
    async def test_transforms_text_delta_to_uppercase(self):
        """on_anthropic_stream_event converts text_delta text to uppercase."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        text_delta = TextDelta.model_construct(type="text_delta", text="hello world")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert len(result) == 1
        result_event = cast(RawContentBlockDeltaEvent, result[0])
        assert result_event.type == "content_block_delta"
        assert isinstance(result_event.delta, TextDelta)
        assert result_event.delta.text == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_does_not_mutate_original_event(self):
        """on_anthropic_stream_event creates new event instead of mutating original."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        original_text = "hello world"
        text_delta = TextDelta.model_construct(type="text_delta", text=original_text)
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert len(result) == 1
        assert result[0] is not event
        assert event.delta.text == original_text
        result_event = cast(RawContentBlockDeltaEvent, result[0])
        assert result_event.delta.text == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_leaves_thinking_delta_unchanged(self):
        """on_anthropic_stream_event does not modify thinking_delta events."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        thinking_delta = ThinkingDelta.model_construct(type="thinking_delta", thinking="Let me consider...")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=thinking_delta,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert len(result) == 1
        result_event = cast(RawContentBlockDeltaEvent, result[0])
        assert isinstance(result_event.delta, ThinkingDelta)
        assert result_event.delta.thinking == "Let me consider..."

    @pytest.mark.asyncio
    async def test_leaves_input_json_delta_unchanged(self):
        """on_anthropic_stream_event does not modify input_json_delta events."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        json_delta = InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"loc')
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=json_delta,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert len(result) == 1
        result_event = cast(RawContentBlockDeltaEvent, result[0])
        assert isinstance(result_event.delta, InputJSONDelta)
        assert result_event.delta.partial_json == '{"loc'

    @pytest.mark.asyncio
    async def test_passes_through_message_start(self):
        """on_anthropic_stream_event passes through message_start events unchanged."""
        policy = AllCapsPolicy()
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
    async def test_passes_through_content_block_start(self):
        """on_anthropic_stream_event passes through content_block_start events unchanged."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block={"type": "text", "text": ""},
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_passes_through_content_block_stop(self):
        """on_anthropic_stream_event passes through content_block_stop events unchanged."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_passes_through_message_delta(self):
        """on_anthropic_stream_event passes through message_delta events unchanged."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 10},
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_passes_through_message_stop(self):
        """on_anthropic_stream_event passes through message_stop events unchanged."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageStopEvent.model_construct(type="message_stop")

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_never_returns_empty_list(self):
        """on_anthropic_stream_event never filters out events (returns empty list)."""
        policy = AllCapsPolicy()
        ctx = PolicyContext.for_testing()

        events: list = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": DEFAULT_TEST_MODEL,
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            ),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=0,
                content_block={"type": "text", "text": ""},
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="Hi"),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
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


__all__ = [
    "TestAllCapsPolicyProtocol",
    "TestAllCapsPolicyOpenAINonStreaming",
    "TestAllCapsPolicyOpenAIStreaming",
    "TestAllCapsPolicyOpenAIRequest",
    "TestAllCapsPolicyAnthropicRequest",
    "TestAllCapsPolicyAnthropicResponse",
    "TestAllCapsPolicyAnthropicStreamEvent",
]
