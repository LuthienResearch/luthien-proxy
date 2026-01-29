"""Unit tests for Anthropic SSE assembler.

These tests validate the AnthropicSSEAssembler by feeding it sequences of
ModelResponse objects and checking the output events. No mocking - just real objects.
"""

from unittest.mock import patch

import pytest
from litellm.types.utils import ChatCompletionDeltaToolCall, Function
from tests.unit_tests.helpers.litellm_test_utils import make_streaming_chunk

from luthien_proxy.streaming.client_formatter.anthropic_sse_assembler import AnthropicSSEAssembler


class TestAnthropicSSEAssembler:
    """Test the Anthropic SSE event assembler."""

    def test_simple_text_response(self):
        """Test a simple text-only streaming response."""
        tracker = AnthropicSSEAssembler()

        # First chunk with text content
        chunk1 = make_streaming_chunk(content="Hello", model="claude-3-5-haiku-20241022", id="msg_123")

        events1 = tracker.process_chunk(chunk1)

        # Should emit content_block_start + content_block_delta
        assert len(events1) == 2
        assert events1[0]["type"] == "content_block_start"
        assert events1[0]["index"] == 0
        assert events1[0]["content_block"]["type"] == "text"
        assert events1[1]["type"] == "content_block_delta"
        assert events1[1]["index"] == 0
        assert events1[1]["delta"]["type"] == "text_delta"
        assert events1[1]["delta"]["text"] == "Hello"

    def test_text_continuation(self):
        """Test that subsequent text chunks only emit deltas."""
        tracker = AnthropicSSEAssembler()

        # First chunk starts the block
        chunk1 = make_streaming_chunk(content="Hello", model="claude-3-5-haiku-20241022", id="msg_123")
        tracker.process_chunk(chunk1)

        # Second chunk should only emit delta
        chunk2 = make_streaming_chunk(content=" world", model="claude-3-5-haiku-20241022", id="msg_123")

        events2 = tracker.process_chunk(chunk2)

        assert len(events2) == 1
        assert events2[0]["type"] == "content_block_delta"
        assert events2[0]["index"] == 0
        assert events2[0]["delta"]["text"] == " world"

    def test_finish_with_message_delta(self):
        """Test that finish_reason emits message_delta and closes the block."""
        tracker = AnthropicSSEAssembler()

        # Start with text
        chunk1 = make_streaming_chunk(content="Hi", model="claude-3-5-haiku-20241022", id="msg_123")
        tracker.process_chunk(chunk1)

        # Finish chunk with usage in _hidden_params
        chunk2 = make_streaming_chunk(
            content=None, model="claude-3-5-haiku-20241022", id="msg_123", finish_reason="stop"
        )
        # Add usage to hidden params
        chunk2._hidden_params = {"usage": type("Usage", (), {"prompt_tokens": 10, "completion_tokens": 5})()}

        events2 = tracker.process_chunk(chunk2)

        # Should emit content_block_stop + message_delta
        assert len(events2) == 2
        assert events2[0]["type"] == "content_block_stop"
        assert events2[0]["index"] == 0
        assert events2[1]["type"] == "message_delta"
        assert events2[1]["delta"]["stop_reason"] == "end_turn"
        assert events2[1]["usage"]["input_tokens"] == 10
        assert events2[1]["usage"]["output_tokens"] == 5

    def test_tool_use_with_progressive_args(self):
        """Test tool call with progressive argument streaming."""
        tracker = AnthropicSSEAssembler()

        # Text content first
        chunk1 = make_streaming_chunk(content="Let me check", model="claude-3-5-haiku-20241022", id="msg_123")
        tracker.process_chunk(chunk1)

        # Tool call start (has id and name, empty args)
        chunk2 = make_streaming_chunk(
            content="",
            model="claude-3-5-haiku-20241022",
            id="msg_123",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id="toolu_123",
                    function=Function(name="get_weather", arguments=""),
                    type="function",
                    index=0,
                )
            ],
        )

        events2 = tracker.process_chunk(chunk2)

        # Should close text block and start tool block
        assert len(events2) == 2
        assert events2[0]["type"] == "content_block_stop"
        assert events2[0]["index"] == 0
        assert events2[1]["type"] == "content_block_start"
        assert events2[1]["index"] == 1
        assert events2[1]["content_block"]["type"] == "tool_use"
        assert events2[1]["content_block"]["id"] == "toolu_123"
        assert events2[1]["content_block"]["name"] == "get_weather"

        # Empty tool call chunk (no id, empty args)
        chunk3 = make_streaming_chunk(
            content="",
            model="claude-3-5-haiku-20241022",
            id="msg_123",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id=None,
                    function=Function(name=None, arguments=""),
                    type="function",
                    index=0,
                )
            ],
        )

        events3 = tracker.process_chunk(chunk3)

        # Should emit empty input_json_delta
        assert len(events3) == 1
        assert events3[0]["type"] == "content_block_delta"
        assert events3[0]["index"] == 1
        assert events3[0]["delta"]["type"] == "input_json_delta"
        assert events3[0]["delta"]["partial_json"] == ""

        # Progressive argument chunks
        chunk4 = make_streaming_chunk(
            content="",
            model="claude-3-5-haiku-20241022",
            id="msg_123",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id=None,
                    function=Function(name=None, arguments='{"location"'),
                    type="function",
                    index=0,
                )
            ],
        )

        events4 = tracker.process_chunk(chunk4)

        assert len(events4) == 1
        assert events4[0]["type"] == "content_block_delta"
        assert events4[0]["index"] == 1
        assert events4[0]["delta"]["partial_json"] == '{"location"'

    def test_complete_tool_call_buffered(self):
        """Test complete tool call in one chunk (from buffered policy)."""
        tracker = AnthropicSSEAssembler()

        # Text first
        chunk1 = make_streaming_chunk(content="Sure", model="claude-3-5-haiku-20241022", id="msg_123")
        tracker.process_chunk(chunk1)

        # Complete tool call (has both id and args)
        chunk2 = make_streaming_chunk(
            content="",
            model="claude-3-5-haiku-20241022",
            id="msg_123",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id="toolu_456",
                    function=Function(name="search", arguments='{"query":"test"}'),
                    type="function",
                    index=0,
                )
            ],
        )

        events2 = tracker.process_chunk(chunk2)

        # Should emit: content_block_stop (text), content_block_start (tool),
        # content_block_delta (args), content_block_stop (tool)
        assert len(events2) == 4
        assert events2[0]["type"] == "content_block_stop"
        assert events2[0]["index"] == 0
        assert events2[1]["type"] == "content_block_start"
        assert events2[1]["index"] == 1
        assert events2[1]["content_block"]["type"] == "tool_use"
        assert events2[2]["type"] == "content_block_delta"
        assert events2[2]["index"] == 1
        assert events2[2]["delta"]["type"] == "input_json_delta"
        assert events2[2]["delta"]["partial_json"] == '{"query":"test"}'
        assert events2[3]["type"] == "content_block_stop"
        assert events2[3]["index"] == 1

    def test_complete_tool_call_with_finish_reason_emits_message_delta(self):
        """Test that complete tool call with finish_reason emits message_delta.

        This is critical for clients like Claude Code that rely on message_delta
        with stop_reason='tool_use' to know the tool call stream is complete.
        """
        tracker = AnthropicSSEAssembler()

        # Complete tool call with finish_reason in the same chunk
        chunk = make_streaming_chunk(
            content=None,
            model="claude-3-5-haiku-20241022",
            id="msg_123",
            finish_reason="tool_calls",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id="toolu_789",
                    function=Function(name="get_weather", arguments='{"location":"NYC"}'),
                    type="function",
                    index=0,
                )
            ],
        )

        events = tracker.process_chunk(chunk)

        # Should emit: content_block_start, content_block_delta, content_block_stop, message_delta
        assert len(events) == 4
        assert events[0]["type"] == "content_block_start"
        assert events[0]["content_block"]["type"] == "tool_use"
        assert events[0]["content_block"]["id"] == "toolu_789"
        assert events[1]["type"] == "content_block_delta"
        assert events[1]["delta"]["type"] == "input_json_delta"
        assert events[2]["type"] == "content_block_stop"
        assert events[3]["type"] == "message_delta"
        assert events[3]["delta"]["stop_reason"] == "tool_use"
        assert events[3]["delta"]["stop_sequence"] is None

    def test_multiple_content_blocks(self):
        """Test that indices increment correctly for multiple blocks."""
        tracker = AnthropicSSEAssembler()

        # Block 0: text
        chunk1 = make_streaming_chunk(content="First", model="claude-3-5-haiku-20241022", id="msg_123")
        events1 = tracker.process_chunk(chunk1)
        assert events1[0]["index"] == 0  # content_block_start

        # Block 1: tool (complete)
        chunk2 = make_streaming_chunk(
            content=None,
            model="claude-3-5-haiku-20241022",
            id="msg_123",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id="tool_1",
                    function=Function(name="fn", arguments="{}"),
                    type="function",
                    index=0,
                )
            ],
        )
        events2 = tracker.process_chunk(chunk2)
        # First event closes block 0, second starts block 1
        assert events2[0]["type"] == "content_block_stop"
        assert events2[0]["index"] == 0
        assert events2[1]["type"] == "content_block_start"
        assert events2[1]["index"] == 1

    def test_finish_reason_tool_use(self):
        """Test that finish_reason='tool_calls' maps to stop_reason='tool_use'."""
        tracker = AnthropicSSEAssembler()

        # Start with text
        chunk1 = make_streaming_chunk(content="Hi", model="claude-3-5-haiku-20241022", id="msg_123")
        tracker.process_chunk(chunk1)

        # Finish with tool_calls
        chunk2 = make_streaming_chunk(
            content=None, model="claude-3-5-haiku-20241022", id="msg_123", finish_reason="tool_calls"
        )
        chunk2._hidden_params = {"usage": type("Usage", (), {"prompt_tokens": 5, "completion_tokens": 3})()}

        events2 = tracker.process_chunk(chunk2)

        assert events2[1]["type"] == "message_delta"
        assert events2[1]["delta"]["stop_reason"] == "tool_use"

    def test_empty_chunk_with_no_data(self):
        """Test that chunks with no meaningful data are handled gracefully."""
        tracker = AnthropicSSEAssembler()

        # Start a block
        chunk1 = make_streaming_chunk(content="Hi", model="claude-3-5-haiku-20241022", id="msg_123")
        tracker.process_chunk(chunk1)

        # Empty chunk (no content, no tool_calls, no finish_reason)
        chunk2 = make_streaming_chunk(content="", model="claude-3-5-haiku-20241022", id="msg_123")

        events2 = tracker.process_chunk(chunk2)

        # Should emit empty text_delta (fallback behavior)
        assert len(events2) == 1
        assert events2[0]["type"] == "content_block_delta"
        assert events2[0]["delta"]["type"] == "text_delta"
        assert events2[0]["delta"]["text"] == ""

    def test_unexpected_event_type_raises(self):
        """Test that unexpected event types from converter raise ValueError."""
        tracker = AnthropicSSEAssembler()

        chunk = make_streaming_chunk(content="Hi", model="claude-3-5-haiku-20241022", id="msg_123")

        # Mock the converter to return an unexpected event type
        # Patch the static method on the assembler class
        with patch.object(
            AnthropicSSEAssembler,
            "convert_chunk_to_event",
            return_value={"type": "unexpected_event_type", "data": "something"},
        ):
            with pytest.raises(ValueError, match="Unexpected event type from converter: unexpected_event_type"):
                tracker.process_chunk(chunk)


@pytest.mark.parametrize(
    "chunks,expected_event_types",
    [
        # Simple text stream
        (
            [
                make_streaming_chunk(content="A", model="claude", id="msg_1"),
                make_streaming_chunk(content="B", model="claude", id="msg_1"),
            ],
            [
                ["content_block_start", "content_block_delta"],  # First chunk
                ["content_block_delta"],  # Second chunk
            ],
        ),
        # Text then finish
        (
            [
                make_streaming_chunk(content="X", model="claude", id="msg_2"),
                make_streaming_chunk(content=None, model="claude", id="msg_2", finish_reason="stop"),
            ],
            [
                ["content_block_start", "content_block_delta"],
                ["content_block_stop", "message_delta"],
            ],
        ),
    ],
)
def test_event_sequences(chunks, expected_event_types):
    """Parameterized test for various chunk sequences."""
    tracker = AnthropicSSEAssembler()

    for i, chunk in enumerate(chunks):
        # Add usage to finish chunks if needed
        if chunk.choices[0].finish_reason:
            chunk._hidden_params = {"usage": type("Usage", (), {"prompt_tokens": 1, "completion_tokens": 1})()}

        events = tracker.process_chunk(chunk)
        actual_types = [e["type"] for e in events]

        assert actual_types == expected_event_types[i], f"Chunk {i} event types don't match"


class TestConvertChunkToEvent:
    """Dedicated tests for the chunk conversion logic.

    This tests the stateless OpenAI → Anthropic event conversion thoroughly,
    independent of the state management in process_chunk().
    """

    def test_text_content_chunk(self):
        """Test converting a chunk with text content."""
        chunk = make_streaming_chunk(content="Hello world", model="claude", id="msg_123")

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "text_delta"
        assert event["delta"]["text"] == "Hello world"

    def test_empty_text_content(self):
        """Test chunk with empty string content."""
        chunk = make_streaming_chunk(content="", model="claude", id="msg_123")

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "text_delta"
        assert event["delta"]["text"] == ""

    def test_none_content(self):
        """Test chunk with None content falls through to default."""
        chunk = make_streaming_chunk(content=None, model="claude", id="msg_123")

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        # Should return default empty text delta
        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "text_delta"
        assert event["delta"]["text"] == ""

    def test_tool_call_with_id_and_args(self):
        """Test complete tool call in one chunk (buffered case)."""
        chunk = make_streaming_chunk(
            content=None,
            model="claude",
            id="msg_123",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id="toolu_123",
                    function=Function(name="search", arguments='{"query":"test"}'),
                    type="function",
                    index=0,
                )
            ],
        )

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_start"
        assert event["content_block"]["type"] == "tool_use"
        assert event["content_block"]["id"] == "toolu_123"
        assert event["content_block"]["name"] == "search"
        assert event["content_block"]["input"] == {}
        assert event["_complete_tool_call"] is True
        assert event["_arguments"] == '{"query":"test"}'

    def test_tool_call_start_only_id(self):
        """Test tool call start with just ID (progressive streaming)."""
        chunk = make_streaming_chunk(
            content=None,
            model="claude",
            id="msg_123",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id="toolu_456",
                    function=Function(name="get_weather", arguments=""),
                    type="function",
                    index=0,
                )
            ],
        )

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_start"
        assert event["content_block"]["type"] == "tool_use"
        assert event["content_block"]["id"] == "toolu_456"
        assert event["content_block"]["name"] == "get_weather"
        assert "_complete_tool_call" not in event

    def test_tool_call_arguments_only(self):
        """Test tool call arguments delta (no id)."""
        chunk = make_streaming_chunk(
            content=None,
            model="claude",
            id="msg_123",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id=None,
                    function=Function(name=None, arguments='{"location"'),
                    type="function",
                    index=0,
                )
            ],
        )

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "input_json_delta"
        assert event["delta"]["partial_json"] == '{"location"'

    def test_tool_call_empty_arguments(self):
        """Test tool call with empty arguments (placeholder chunk)."""
        chunk = make_streaming_chunk(
            content=None,
            model="claude",
            id="msg_123",
            tool_calls=[
                ChatCompletionDeltaToolCall(
                    id=None,
                    function=Function(name=None, arguments=""),
                    type="function",
                    index=0,
                )
            ],
        )

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "input_json_delta"
        assert event["delta"]["partial_json"] == ""

    def test_finish_reason_stop(self):
        """Test chunk with finish_reason='stop'."""
        chunk = make_streaming_chunk(content=None, model="claude", id="msg_123", finish_reason="stop")
        chunk._hidden_params = {"usage": type("Usage", (), {"prompt_tokens": 10, "completion_tokens": 20})()}

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "message_delta"
        assert event["delta"]["stop_reason"] == "end_turn"
        assert event["delta"]["stop_sequence"] is None
        assert event["usage"]["input_tokens"] == 10
        assert event["usage"]["output_tokens"] == 20

    def test_finish_reason_tool_calls(self):
        """Test chunk with finish_reason='tool_calls'."""
        chunk = make_streaming_chunk(content=None, model="claude", id="msg_123", finish_reason="tool_calls")
        chunk._hidden_params = {"usage": type("Usage", (), {"prompt_tokens": 5, "completion_tokens": 15})()}

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "message_delta"
        assert event["delta"]["stop_reason"] == "tool_use"
        assert event["usage"]["input_tokens"] == 5
        assert event["usage"]["output_tokens"] == 15

    def test_finish_reason_length(self):
        """Test chunk with finish_reason='length'."""
        chunk = make_streaming_chunk(content=None, model="claude", id="msg_123", finish_reason="length")
        chunk._hidden_params = {"usage": type("Usage", (), {"prompt_tokens": 100, "completion_tokens": 4096})()}

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "message_delta"
        assert event["delta"]["stop_reason"] == "max_tokens"

    def test_finish_reason_without_usage(self):
        """Test finish chunk without usage info in hidden_params."""
        chunk = make_streaming_chunk(content=None, model="claude", id="msg_123", finish_reason="stop")
        # No _hidden_params set

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        assert event["type"] == "message_delta"
        assert event["usage"]["output_tokens"] == 0

    def test_unknown_finish_reason(self):
        """Test chunk with unmapped finish_reason falls through."""
        chunk = make_streaming_chunk(content=None, model="claude", id="msg_123", finish_reason="content_filter")
        chunk._hidden_params = {"usage": type("Usage", (), {"prompt_tokens": 1, "completion_tokens": 1})()}

        event = AnthropicSSEAssembler.convert_chunk_to_event(chunk)

        # Should pass through unmapped reason as-is
        assert event["delta"]["stop_reason"] == "content_filter"
