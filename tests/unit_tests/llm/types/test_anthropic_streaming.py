# ABOUTME: Unit tests for Anthropic streaming event TypedDict definitions
# ABOUTME: Tests type compatibility and JSON serialization of streaming events

"""Tests for Anthropic streaming event types."""

import json
from typing import cast

import pytest


class TestAnthropicStreamingEventImports:
    """Test that all streaming event types can be imported."""

    def test_import_message_start_event(self):
        """Test AnthropicMessageStartEvent import."""
        from luthien_proxy.llm.types.anthropic import AnthropicMessageStartEvent

        assert AnthropicMessageStartEvent is not None

    def test_import_content_block_start_event(self):
        """Test AnthropicContentBlockStartEvent import."""
        from luthien_proxy.llm.types.anthropic import AnthropicContentBlockStartEvent

        assert AnthropicContentBlockStartEvent is not None

    def test_import_content_block_delta_event(self):
        """Test AnthropicContentBlockDeltaEvent import."""
        from luthien_proxy.llm.types.anthropic import AnthropicContentBlockDeltaEvent

        assert AnthropicContentBlockDeltaEvent is not None

    def test_import_content_block_stop_event(self):
        """Test AnthropicContentBlockStopEvent import."""
        from luthien_proxy.llm.types.anthropic import AnthropicContentBlockStopEvent

        assert AnthropicContentBlockStopEvent is not None

    def test_import_message_delta_event(self):
        """Test AnthropicMessageDeltaEvent import."""
        from luthien_proxy.llm.types.anthropic import AnthropicMessageDeltaEvent

        assert AnthropicMessageDeltaEvent is not None

    def test_import_message_stop_event(self):
        """Test AnthropicMessageStopEvent import."""
        from luthien_proxy.llm.types.anthropic import AnthropicMessageStopEvent

        assert AnthropicMessageStopEvent is not None

    def test_import_streaming_event_union(self):
        """Test AnthropicStreamingEvent union type import."""
        from luthien_proxy.llm.types.anthropic import AnthropicStreamingEvent

        assert AnthropicStreamingEvent is not None


class TestAnthropicDeltaTypes:
    """Test delta type definitions for streaming content."""

    def test_import_text_delta(self):
        """Test AnthropicTextDelta import."""
        from luthien_proxy.llm.types.anthropic import AnthropicTextDelta

        assert AnthropicTextDelta is not None

    def test_import_thinking_delta(self):
        """Test AnthropicThinkingDelta import."""
        from luthien_proxy.llm.types.anthropic import AnthropicThinkingDelta

        assert AnthropicThinkingDelta is not None

    def test_import_input_json_delta(self):
        """Test AnthropicInputJSONDelta import."""
        from luthien_proxy.llm.types.anthropic import AnthropicInputJSONDelta

        assert AnthropicInputJSONDelta is not None

    def test_import_signature_delta(self):
        """Test AnthropicSignatureDelta import."""
        from luthien_proxy.llm.types.anthropic import AnthropicSignatureDelta

        assert AnthropicSignatureDelta is not None


class TestMessageStartEventStructure:
    """Test AnthropicMessageStartEvent structure and JSON serialization."""

    def test_message_start_basic_structure(self):
        """Test basic message_start event can be created as dict."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicMessageStartEvent,
            AnthropicStreamingMessage,
        )

        message: AnthropicStreamingMessage = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-3-5-sonnet-20241022",
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }
        event: AnthropicMessageStartEvent = {
            "type": "message_start",
            "message": message,
        }

        assert event["type"] == "message_start"
        assert event["message"]["id"] == "msg_123"
        assert event["message"]["role"] == "assistant"

    def test_message_start_json_serializable(self):
        """Test message_start event serializes to valid JSON."""
        from luthien_proxy.llm.types.anthropic import AnthropicMessageStartEvent

        event: AnthropicMessageStartEvent = {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-3-5-sonnet-20241022",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }

        json_str = json.dumps(event)
        parsed = json.loads(json_str)
        assert parsed["type"] == "message_start"
        assert parsed["message"]["id"] == "msg_test"


class TestContentBlockStartEventStructure:
    """Test AnthropicContentBlockStartEvent structure."""

    def test_text_block_start(self):
        """Test content_block_start for text block."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlockStartEvent,
            AnthropicTextBlock,
        )

        text_block: AnthropicTextBlock = {"type": "text", "text": ""}
        event: AnthropicContentBlockStartEvent = {
            "type": "content_block_start",
            "index": 0,
            "content_block": text_block,
        }

        assert event["type"] == "content_block_start"
        assert event["index"] == 0
        assert event["content_block"]["type"] == "text"

    def test_thinking_block_start(self):
        """Test content_block_start for thinking block."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlockStartEvent,
            AnthropicStreamingThinkingBlock,
        )

        thinking_block: AnthropicStreamingThinkingBlock = {
            "type": "thinking",
            "thinking": "",
        }
        event: AnthropicContentBlockStartEvent = {
            "type": "content_block_start",
            "index": 0,
            "content_block": thinking_block,
        }

        assert event["content_block"]["type"] == "thinking"

    def test_tool_use_block_start(self):
        """Test content_block_start for tool_use block."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlockStartEvent,
            AnthropicStreamingToolUseBlock,
        )

        tool_block: AnthropicStreamingToolUseBlock = {
            "type": "tool_use",
            "id": "toolu_123",
            "name": "get_weather",
            "input": {},
        }
        event: AnthropicContentBlockStartEvent = {
            "type": "content_block_start",
            "index": 1,
            "content_block": tool_block,
        }

        assert event["content_block"]["type"] == "tool_use"
        assert event["content_block"]["id"] == "toolu_123"

    def test_redacted_thinking_block_start(self):
        """Test content_block_start for redacted_thinking block."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlockStartEvent,
            AnthropicRedactedThinkingBlock,
        )

        redacted_block: AnthropicRedactedThinkingBlock = {
            "type": "redacted_thinking",
            "data": "encrypted_data_here",
        }
        event: AnthropicContentBlockStartEvent = {
            "type": "content_block_start",
            "index": 0,
            "content_block": redacted_block,
        }

        assert event["content_block"]["type"] == "redacted_thinking"


class TestContentBlockDeltaEventStructure:
    """Test AnthropicContentBlockDeltaEvent structure."""

    def test_text_delta(self):
        """Test content_block_delta with text content."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlockDeltaEvent,
            AnthropicTextDelta,
        )

        delta: AnthropicTextDelta = {"type": "text_delta", "text": "Hello"}
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": delta,
        }

        assert event["type"] == "content_block_delta"
        assert event["index"] == 0
        assert event["delta"]["type"] == "text_delta"
        assert event["delta"]["text"] == "Hello"

    def test_thinking_delta(self):
        """Test content_block_delta with thinking content."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlockDeltaEvent,
            AnthropicThinkingDelta,
        )

        delta: AnthropicThinkingDelta = {
            "type": "thinking_delta",
            "thinking": "Let me analyze this...",
        }
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": delta,
        }

        assert event["delta"]["type"] == "thinking_delta"
        assert event["delta"]["thinking"] == "Let me analyze this..."

    def test_input_json_delta(self):
        """Test content_block_delta with tool input JSON."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlockDeltaEvent,
            AnthropicInputJSONDelta,
        )

        delta: AnthropicInputJSONDelta = {
            "type": "input_json_delta",
            "partial_json": '{"city": "San',
        }
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 1,
            "delta": delta,
        }

        assert event["delta"]["type"] == "input_json_delta"
        assert event["delta"]["partial_json"] == '{"city": "San'

    def test_signature_delta(self):
        """Test content_block_delta with thinking signature."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlockDeltaEvent,
            AnthropicSignatureDelta,
        )

        delta: AnthropicSignatureDelta = {
            "type": "signature_delta",
            "signature": "abc123signature",
        }
        event: AnthropicContentBlockDeltaEvent = {
            "type": "content_block_delta",
            "index": 0,
            "delta": delta,
        }

        assert event["delta"]["type"] == "signature_delta"
        assert event["delta"]["signature"] == "abc123signature"


class TestContentBlockStopEventStructure:
    """Test AnthropicContentBlockStopEvent structure."""

    def test_content_block_stop(self):
        """Test content_block_stop event."""
        from luthien_proxy.llm.types.anthropic import AnthropicContentBlockStopEvent

        event: AnthropicContentBlockStopEvent = {
            "type": "content_block_stop",
            "index": 0,
        }

        assert event["type"] == "content_block_stop"
        assert event["index"] == 0

    def test_content_block_stop_json_serializable(self):
        """Test content_block_stop event serializes to valid JSON."""
        from luthien_proxy.llm.types.anthropic import AnthropicContentBlockStopEvent

        event: AnthropicContentBlockStopEvent = {
            "type": "content_block_stop",
            "index": 2,
        }

        json_str = json.dumps(event)
        parsed = json.loads(json_str)
        assert parsed["type"] == "content_block_stop"
        assert parsed["index"] == 2


class TestMessageDeltaEventStructure:
    """Test AnthropicMessageDeltaEvent structure."""

    def test_message_delta_end_turn(self):
        """Test message_delta with end_turn stop reason."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicMessageDelta,
            AnthropicMessageDeltaEvent,
            AnthropicMessageDeltaUsage,
        )

        delta: AnthropicMessageDelta = {
            "stop_reason": "end_turn",
            "stop_sequence": None,
        }
        usage: AnthropicMessageDeltaUsage = {"output_tokens": 42}
        event: AnthropicMessageDeltaEvent = {
            "type": "message_delta",
            "delta": delta,
            "usage": usage,
        }

        assert event["type"] == "message_delta"
        assert event["delta"]["stop_reason"] == "end_turn"
        assert event["usage"]["output_tokens"] == 42

    def test_message_delta_tool_use(self):
        """Test message_delta with tool_use stop reason."""
        from luthien_proxy.llm.types.anthropic import AnthropicMessageDeltaEvent

        event: AnthropicMessageDeltaEvent = {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 100},
        }

        assert event["delta"]["stop_reason"] == "tool_use"

    def test_message_delta_max_tokens(self):
        """Test message_delta with max_tokens stop reason."""
        from luthien_proxy.llm.types.anthropic import AnthropicMessageDeltaEvent

        event: AnthropicMessageDeltaEvent = {
            "type": "message_delta",
            "delta": {"stop_reason": "max_tokens", "stop_sequence": None},
            "usage": {"output_tokens": 4096},
        }

        assert event["delta"]["stop_reason"] == "max_tokens"


class TestMessageStopEventStructure:
    """Test AnthropicMessageStopEvent structure."""

    def test_message_stop(self):
        """Test message_stop event."""
        from luthien_proxy.llm.types.anthropic import AnthropicMessageStopEvent

        event: AnthropicMessageStopEvent = {"type": "message_stop"}

        assert event["type"] == "message_stop"

    def test_message_stop_json_serializable(self):
        """Test message_stop event serializes to valid JSON."""
        from luthien_proxy.llm.types.anthropic import AnthropicMessageStopEvent

        event: AnthropicMessageStopEvent = {"type": "message_stop"}

        json_str = json.dumps(event)
        parsed = json.loads(json_str)
        assert parsed["type"] == "message_stop"


class TestStreamingEventUnion:
    """Test that AnthropicStreamingEvent union works correctly."""

    @pytest.mark.parametrize(
        "event_type,expected_type_field",
        [
            ("message_start", "message_start"),
            ("content_block_start", "content_block_start"),
            ("content_block_delta", "content_block_delta"),
            ("content_block_stop", "content_block_stop"),
            ("message_delta", "message_delta"),
            ("message_stop", "message_stop"),
        ],
    )
    def test_event_type_discriminator(self, event_type: str, expected_type_field: str):
        """Test that each event type has correct 'type' field."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlockDeltaEvent,
            AnthropicContentBlockStartEvent,
            AnthropicContentBlockStopEvent,
            AnthropicMessageDeltaEvent,
            AnthropicMessageStartEvent,
            AnthropicMessageStopEvent,
        )

        events = {
            "message_start": cast(
                AnthropicMessageStartEvent,
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": "claude-3",
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            ),
            "content_block_start": cast(
                AnthropicContentBlockStartEvent,
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            "content_block_delta": cast(
                AnthropicContentBlockDeltaEvent,
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Hi"},
                },
            ),
            "content_block_stop": cast(
                AnthropicContentBlockStopEvent,
                {"type": "content_block_stop", "index": 0},
            ),
            "message_delta": cast(
                AnthropicMessageDeltaEvent,
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 10},
                },
            ),
            "message_stop": cast(
                AnthropicMessageStopEvent,
                {"type": "message_stop"},
            ),
        }

        event = events[event_type]
        assert event["type"] == expected_type_field


class TestStreamingContentBlockUnion:
    """Test the streaming content block union type."""

    def test_import_streaming_content_block(self):
        """Test AnthropicStreamingContentBlock import."""
        from luthien_proxy.llm.types.anthropic import AnthropicStreamingContentBlock

        assert AnthropicStreamingContentBlock is not None

    @pytest.mark.parametrize(
        "block_type",
        ["text", "thinking", "tool_use", "redacted_thinking"],
    )
    def test_streaming_content_block_types(self, block_type: str):
        """Test that streaming content block union includes expected types."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicRedactedThinkingBlock,
            AnthropicStreamingThinkingBlock,
            AnthropicStreamingToolUseBlock,
            AnthropicTextBlock,
        )

        blocks = {
            "text": cast(AnthropicTextBlock, {"type": "text", "text": ""}),
            "thinking": cast(AnthropicStreamingThinkingBlock, {"type": "thinking", "thinking": ""}),
            "tool_use": cast(
                AnthropicStreamingToolUseBlock,
                {"type": "tool_use", "id": "t1", "name": "fn", "input": {}},
            ),
            "redacted_thinking": cast(
                AnthropicRedactedThinkingBlock,
                {"type": "redacted_thinking", "data": "x"},
            ),
        }

        block = blocks[block_type]
        assert block["type"] == block_type


class TestStreamingDeltaUnion:
    """Test the streaming delta union type."""

    def test_import_streaming_delta(self):
        """Test AnthropicStreamingDelta import."""
        from luthien_proxy.llm.types.anthropic import AnthropicStreamingDelta

        assert AnthropicStreamingDelta is not None

    @pytest.mark.parametrize(
        "delta_type",
        ["text_delta", "thinking_delta", "input_json_delta", "signature_delta"],
    )
    def test_streaming_delta_types(self, delta_type: str):
        """Test that streaming delta union includes expected types."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicInputJSONDelta,
            AnthropicSignatureDelta,
            AnthropicTextDelta,
            AnthropicThinkingDelta,
        )

        deltas = {
            "text_delta": cast(AnthropicTextDelta, {"type": "text_delta", "text": ""}),
            "thinking_delta": cast(AnthropicThinkingDelta, {"type": "thinking_delta", "thinking": ""}),
            "input_json_delta": cast(AnthropicInputJSONDelta, {"type": "input_json_delta", "partial_json": ""}),
            "signature_delta": cast(AnthropicSignatureDelta, {"type": "signature_delta", "signature": ""}),
        }

        delta = deltas[delta_type]
        assert delta["type"] == delta_type
