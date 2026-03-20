"""Unit tests for StringReplacementPolicy.

Tests native Anthropic API support.
"""

from typing import Any, cast

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

from conftest import DEFAULT_TEST_MODEL
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
from luthien_proxy.policy_core import AnthropicExecutionInterface
from luthien_proxy.policy_core.policy_context import PolicyContext


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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "hi HI Hi"


async def _collect_stream_text(
    policy: StringReplacementPolicy,
    ctx: PolicyContext,
    events: list[Any],
) -> str:
    """Send events through the policy and collect all emitted text.

    Calls on_anthropic_stream_event for each event, then on_anthropic_stream_complete
    to flush any remaining buffer.
    """
    parts: list[str] = []
    for event in events:
        result = await policy.on_anthropic_stream_event(event, ctx)
        for ev in result:
            if isinstance(ev, RawContentBlockDeltaEvent) and isinstance(ev.delta, TextDelta):
                parts.append(ev.delta.text)
    for ev in await policy.on_anthropic_stream_complete(ctx):
        if isinstance(ev, RawContentBlockDeltaEvent) and isinstance(ev.delta, TextDelta):
            parts.append(ev.delta.text)
    return "".join(parts)


class TestAnthropicStreamEvent:
    """Tests for on_anthropic_stream_event text delta transformation behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_transforms_text_delta(self):
        """on_anthropic_stream_event applies replacement to text_delta text, flushing on block stop."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        ctx = PolicyContext.for_testing()

        text_delta = TextDelta.model_construct(type="text_delta", text="hello foo world")
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )
        stop_event = RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0)

        full_text = await _collect_stream_text(policy, ctx, [delta_event, stop_event])

        assert full_text == "hello bar world"

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

        # Original event should be unchanged
        assert event.delta.text == original_text
        # Result events should have different objects
        for ev in result:
            assert ev is not event

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
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )
        stop_event = RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0)

        full_text = await _collect_stream_text(policy, ctx, [delta_event, stop_event])

        assert full_text == "GOODBYE world"

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
                "model": DEFAULT_TEST_MODEL,
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
        """content_block_stop passes through when the buffer is empty (no prior text deltas)."""
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        result_text_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_text_block["text"] == "Hello check world!"


class TestStreamingBufferBehavior:
    """Tests for cross-chunk buffering in streaming mode."""

    @pytest.mark.asyncio
    async def test_replacement_spanning_two_chunks(self):
        """Replacement target split across two chunks is correctly replaced."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["hello", "goodbye"]]))
        ctx = PolicyContext.for_testing()

        events = [
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="say hel"),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="lo there"),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
        ]

        full_text = await _collect_stream_text(policy, ctx, events)
        assert full_text == "say goodbye there"

    @pytest.mark.asyncio
    async def test_one_char_at_a_time(self):
        """Replacement works even when text arrives one character at a time."""
        policy = StringReplacementPolicy(
            config=StringReplacementConfig(
                replacements=[["hello", "hi"]],
                match_capitalization=True,
            )
        )
        ctx = PolicyContext.for_testing()

        text = "say Hello!"
        events: list[Any] = [
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text=c),
            )
            for c in text
        ]
        events.append(RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0))

        full_text = await _collect_stream_text(policy, ctx, events)
        assert full_text == "say Hi!"

    @pytest.mark.asyncio
    async def test_multiple_replacements_across_chunks(self):
        """Multiple different replacement targets spanning chunks all work."""
        policy = StringReplacementPolicy(
            config=StringReplacementConfig(
                replacements=[["apple", "orange"], ["grape", "melon"]],
                match_capitalization=True,
            )
        )
        ctx = PolicyContext.for_testing()

        # "apple" split as "app" + "le", "grape" split as "gra" + "pe"
        events = [
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="I like app"),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="le and gra"),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="pe juice"),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
        ]

        full_text = await _collect_stream_text(policy, ctx, events)
        assert full_text == "I like orange and melon juice"

    @pytest.mark.asyncio
    async def test_no_buffering_for_single_char_replacements(self):
        """Single-char replacements don't use buffering (no chunk boundary issue)."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["a", "x"]]))
        ctx = PolicyContext.for_testing()

        # buffer_size should be 0 for single-char source
        assert policy._buffer_size == 0

        text_delta = TextDelta.model_construct(type="text_delta", text="banana")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)
        assert len(result) == 1
        result_event = cast(RawContentBlockDeltaEvent, result[0])
        result_delta = cast(TextDelta, result_event.delta)
        assert result_delta.text == "bxnxnx"

    @pytest.mark.asyncio
    async def test_buffer_flushed_on_stream_complete(self):
        """on_anthropic_stream_complete flushes any remaining buffer."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["hello", "hi"]]))
        ctx = PolicyContext.for_testing()

        # Send text without a content_block_stop
        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )
        result = await policy.on_anthropic_stream_event(event, ctx)
        parts = [
            ev.delta.text
            for ev in result
            if isinstance(ev, RawContentBlockDeltaEvent) and isinstance(ev.delta, TextDelta)
        ]

        # Flush via stream_complete
        complete_events = await policy.on_anthropic_stream_complete(ctx)
        for ev in complete_events:
            if isinstance(ev, RawContentBlockDeltaEvent) and isinstance(ev.delta, TextDelta):
                parts.append(ev.delta.text)

        assert "".join(parts) == "hi"

    @pytest.mark.asyncio
    async def test_empty_buffer_on_stream_complete(self):
        """on_anthropic_stream_complete returns empty list when no buffer remains."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["foo", "bar"]]))
        ctx = PolicyContext.for_testing()

        result = await policy.on_anthropic_stream_complete(ctx)
        assert result == []

    @pytest.mark.asyncio
    async def test_double_flush_does_not_double_emit(self):
        """content_block_stop flush followed by stream_complete does not emit twice."""
        policy = StringReplacementPolicy(config=StringReplacementConfig(replacements=[["hello", "hi"]]))
        ctx = PolicyContext.for_testing()

        # Send text + content_block_stop (flushes buffer)
        events: list[Any] = [
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="hello"),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
        ]
        full_text = await _collect_stream_text(policy, ctx, events)
        assert full_text == "hi"

        # stream_complete should return nothing — buffer already flushed
        complete_events = await policy.on_anthropic_stream_complete(ctx)
        assert complete_events == []
