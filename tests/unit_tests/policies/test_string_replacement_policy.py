"""Unit tests for StringReplacementPolicy.

Tests both OpenAI-format (via LiteLLM) and native Anthropic API support.
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

from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies.string_replacement_policy import (
    StringReplacementConfig,
    StringReplacementPolicy,
    _apply_capitalization_pattern,
    _detect_capitalization_pattern,
    apply_replacements,
)
from luthien_proxy.policy_core import AnthropicExecutionInterface, OpenAIPolicyInterface
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock
from luthien_proxy.streaming.stream_state import StreamState


@pytest.fixture
def policy_context():
    return PolicyContext(
        transaction_id="test-txn-123",
        request=Request(model="test-model", messages=[{"role": "user", "content": "test"}]),
    )


@pytest.fixture
def streaming_context():
    stream_state = StreamState()
    policy_ctx = PolicyContext(
        transaction_id="test-txn-123",
        request=Request(model="test-model", messages=[{"role": "user", "content": "test"}]),
    )
    return StreamingPolicyContext(
        policy_ctx=policy_ctx,
        egress_queue=asyncio.Queue(),
        original_streaming_response_state=stream_state,
        keepalive=lambda: None,
    )


class TestCapitalizationHelpers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("HELLO", "upper"),
            ("hello", "lower"),
            ("Hello", "title"),
            ("hELLO", "mixed"),
            ("", "lower"),
        ],
    )
    def test_detect_pattern(self, text, expected):
        assert _detect_capitalization_pattern(text) == expected

    @pytest.mark.parametrize(
        "source,replacement,expected",
        [
            ("HELLO", "world", "WORLD"),
            ("hello", "WORLD", "world"),
            ("Hello", "world", "World"),
            ("cOOl", "radicAL", "rADicAL"),
        ],
    )
    def test_apply_pattern(self, source, replacement, expected):
        assert _apply_capitalization_pattern(source, replacement) == expected


class TestApplyReplacements:
    @pytest.mark.parametrize(
        "text,replacements,match_cap,expected",
        [
            ("hello world", [("hello", "goodbye")], False, "goodbye world"),
            ("hello foo", [("hello", "hi"), ("foo", "bar")], False, "hi bar"),
            ("Hello HELLO hello", [("hello", "hi")], True, "Hi HI hi"),
            ("", [("a", "b")], False, ""),
            ("hello", [], False, "hello"),
            ("[test]", [("[test]", "check")], False, "check"),
        ],
    )
    def test_apply_replacements(self, text, replacements, match_cap, expected):
        assert apply_replacements(text, replacements, match_cap) == expected


class TestImplementsInterfaces:
    """Tests verifying StringReplacementPolicy implements the expected interfaces."""

    def test_implements_openai_interface(self):
        """StringReplacementPolicy implements OpenAIPolicyInterface."""
        policy = StringReplacementPolicy()
        assert isinstance(policy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self):
        """StringReplacementPolicy implements AnthropicExecutionInterface."""
        policy = StringReplacementPolicy()
        assert isinstance(policy, AnthropicExecutionInterface)

    def test_get_config_returns_configuration(self):
        """get_config returns the policy configuration."""
        policy = StringReplacementPolicy(
            config=StringReplacementConfig(
                replacements=[["foo", "bar"], ["hello", "goodbye"]],
                match_capitalization=True,
            )
        )
        config = policy.get_config()
        assert config["replacements"] == [["foo", "bar"], ["hello", "goodbye"]]
        assert config["match_capitalization"] is True

    def test_get_config_empty_replacements(self):
        """get_config handles empty replacements."""
        policy = StringReplacementPolicy()
        config = policy.get_config()
        assert config["replacements"] == []
        assert config["match_capitalization"] is False


class TestOpenAINonStreaming:
    @pytest.mark.parametrize(
        "replacements,match_cap,content,expected",
        [
            ([["hello", "goodbye"]], False, "hello world", "goodbye world"),
            ([["hello", "goodbye"]], True, "Hello HELLO hello", "Goodbye GOODBYE goodbye"),
            ([["foo", "bar"]], False, "hello world", "hello world"),
        ],
    )
    async def test_on_openai_response(self, policy_context, replacements, match_cap, content, expected):
        policy = StringReplacementPolicy(
            config=StringReplacementConfig(replacements=replacements, match_capitalization=match_cap)
        )
        response = ModelResponse(
            id="test-id",
            choices=[Choices(finish_reason="stop", index=0, message=Message(content=content, role="assistant"))],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )
        result = await policy.on_openai_response(response, policy_context)
        assert result.choices[0].message.content == expected

    async def test_on_openai_request_passthrough(self, policy_context):
        """on_openai_request returns the exact same request object unchanged."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        request = Request(model="test-model", messages=[{"role": "user", "content": "Hello foo"}])

        result = await policy.on_openai_request(request, policy_context)

        assert result is request


class TestOpenAIStreaming:
    @pytest.mark.parametrize(
        "content,replacements,expected",
        [
            ("hello world", [["hello", "goodbye"]], "goodbye world"),
            ("say hello world please", [["hello world", "goodbye"]], "say goodbye please"),
        ],
    )
    async def test_on_content_complete(self, streaming_context, content, replacements, expected):
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=replacements))

        content_block = ContentStreamBlock(id="content")
        content_block.content = content
        streaming_context.original_streaming_response_state.current_block = content_block

        chunk = make_streaming_chunk(content="", model="test-model", id="test-id", finish_reason=None)
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_content_complete(streaming_context)

        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk.choices[0].delta.content == expected

    async def test_content_deltas_filtered(self, streaming_context):
        """Content delta chunks are buffered, not passed through."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        chunk = make_streaming_chunk(content="hello foo", model="test-model", id="test-id", finish_reason=None)
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_chunk_received(streaming_context)

        assert streaming_context.egress_queue.empty()

    @pytest.mark.parametrize(
        "content,finish_reason,tool_calls",
        [
            (None, "stop", None),  # Finish reason chunk
            (
                None,
                None,
                [{"index": 0, "id": "call_1", "function": {"name": "f", "arguments": "{}"}, "type": "function"}],
            ),
        ],
    )
    async def test_non_content_chunks_pass_through(self, streaming_context, content, finish_reason, tool_calls):
        """Non-content chunks (finish reason, tool calls) pass through immediately."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        chunk = make_streaming_chunk(
            content=content, model="test-model", id="test-id", finish_reason=finish_reason, tool_calls=tool_calls
        )
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

        await policy.on_chunk_received(streaming_context)

        queued_chunk = streaming_context.egress_queue.get_nowait()
        assert queued_chunk == chunk


# -------------------------------------------------------------------------
# Anthropic Interface Tests
# -------------------------------------------------------------------------


class TestAnthropicRequest:
    """Tests for on_anthropic_request passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_request_returns_same_request(self):
        """on_anthropic_request returns the exact same request object unchanged."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello foo"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result is request


class TestAnthropicResponse:
    """Tests for on_anthropic_response string replacement behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_response_applies_replacement(self):
        """on_anthropic_response applies string replacements to text content blocks."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello foo world!"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "Hello bar world!"

    @pytest.mark.asyncio
    async def test_on_anthropic_response_applies_multiple_replacements(self):
        """on_anthropic_response applies multiple string replacements in order."""
        policy = StringReplacementPolicy(
            config=StringReplacementConfig(replacements=[["foo", "bar"], ["hello", "goodbye"]])
        )
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "hello foo world"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "goodbye bar world"

    @pytest.mark.asyncio
    async def test_on_anthropic_response_leaves_tool_use_unchanged(self):
        """on_anthropic_response does not modify tool_use content blocks."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["weather", "climate"]]))
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

        result = await policy.on_anthropic_response(response, ctx)

        result_tool_block = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_on_anthropic_response_mixed_content_blocks(self):
        """on_anthropic_response transforms text but leaves tool_use unchanged in mixed content."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["weather", "climate"]]))
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

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        result_tool_block = cast(AnthropicToolUseBlock, result["content"][1])
        assert result_text_block["text"] == "Let me check the climate"
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"


class TestAnthropicCapitalization:
    """Tests for case-insensitive matching with capitalization preservation."""

    @pytest.mark.asyncio
    async def test_on_anthropic_response_match_capitalization_lowercase(self):
        """on_anthropic_response preserves lowercase capitalization pattern."""
        policy = StringReplacementPolicy(
            config=StringReplacementConfig(
                replacements=[["hello", "goodbye"]],
                match_capitalization=True,
            )
        )
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

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "goodbye world"

    @pytest.mark.asyncio
    async def test_on_anthropic_response_match_capitalization_uppercase(self):
        """on_anthropic_response preserves uppercase capitalization pattern."""
        policy = StringReplacementPolicy(
            config=StringReplacementConfig(
                replacements=[["hello", "goodbye"]],
                match_capitalization=True,
            )
        )
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "HELLO world"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "GOODBYE world"

    @pytest.mark.asyncio
    async def test_on_anthropic_response_match_capitalization_multiple_occurrences(self):
        """on_anthropic_response handles multiple occurrences with different capitalizations."""
        policy = StringReplacementPolicy(
            config=StringReplacementConfig(
                replacements=[["hello", "hi"]],
                match_capitalization=True,
            )
        )
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "hello HELLO Hello"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "hi HI Hi"


class TestAnthropicStreamEvent:
    """Tests for on_anthropic_stream_event text delta transformation behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_transforms_text_delta(self):
        """on_anthropic_stream_event applies replacement to text_delta text."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        ctx = PolicyContext.for_testing()

        text_delta = TextDelta.model_construct(type="text_delta", text="hello foo world")
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
        assert result_event.delta.text == "hello bar world"

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_does_not_mutate_original(self):
        """on_anthropic_stream_event creates new event instead of mutating original."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        ctx = PolicyContext.for_testing()

        original_text = "hello foo world"
        text_delta = TextDelta.model_construct(type="text_delta", text=original_text)
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        # Result should contain a different object
        assert len(result) == 1
        assert result[0] is not event
        # Original event should be unchanged
        assert event.delta.text == original_text
        # Result should have replaced text
        result_event = cast(RawContentBlockDeltaEvent, result[0])
        assert result_event.delta.text == "hello bar world"

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_match_capitalization(self):
        """on_anthropic_stream_event preserves capitalization in streaming deltas."""
        policy = StringReplacementPolicy(
            config=StringReplacementConfig(
                replacements=[["hello", "goodbye"]],
                match_capitalization=True,
            )
        )
        ctx = PolicyContext.for_testing()

        text_delta = TextDelta.model_construct(type="text_delta", text="HELLO world")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert len(result) == 1
        result_event = cast(RawContentBlockDeltaEvent, result[0])
        assert result_event.delta.text == "GOODBYE world"

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_leaves_thinking_delta_unchanged(self):
        """on_anthropic_stream_event does not modify thinking_delta events."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["consider", "think"]]))
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
    async def test_on_anthropic_stream_event_leaves_input_json_delta_unchanged(self):
        """on_anthropic_stream_event does not modify input_json_delta events."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["loc", "location"]]))
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
    async def test_on_anthropic_stream_event_passes_through_message_start(self):
        """on_anthropic_stream_event passes through message_start events unchanged."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["test", "demo"]]))
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

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_content_block_start(self):
        """on_anthropic_stream_event passes through content_block_start events unchanged."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["test", "demo"]]))
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block={"type": "text", "text": ""},
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_content_block_stop(self):
        """on_anthropic_stream_event passes through content_block_stop events unchanged."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["test", "demo"]]))
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_message_delta(self):
        """on_anthropic_stream_event passes through message_delta events unchanged."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["test", "demo"]]))
        ctx = PolicyContext.for_testing()

        event = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 10},
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_message_stop(self):
        """on_anthropic_stream_event passes through message_stop events unchanged."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["test", "demo"]]))
        ctx = PolicyContext.for_testing()

        event = RawMessageStopEvent.model_construct(type="message_stop")

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]


class TestAnthropicEdgeCases:
    """Tests for edge cases and special scenarios."""

    @pytest.mark.asyncio
    async def test_empty_replacements_list(self):
        """Policy with empty replacements list leaves content unchanged."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[]))
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello world!"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "Hello world!"

    @pytest.mark.asyncio
    async def test_none_replacements(self):
        """Policy with None replacements leaves content unchanged."""
        policy = StringReplacementPolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello world!"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "Hello world!"

    @pytest.mark.asyncio
    async def test_empty_content_list(self):
        """Policy handles response with empty content list."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result["content"] == []

    @pytest.mark.asyncio
    async def test_special_regex_characters_in_replacement(self):
        """Policy handles special regex characters in replacement strings."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["[test]", "check"]]))
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello [test] world!"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "Hello check world!"
