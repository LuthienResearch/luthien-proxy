"""Unit tests for streaming converters.

These tests validate the AnthropicStreamStateTracker by feeding it sequences of
ModelResponse objects and checking the output events. No mocking - just real objects.
"""

from unittest.mock import patch

import pytest
from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.v2.llm.streaming_converters import AnthropicStreamStateTracker


class TestAnthropicStreamStateTracker:
    """Test the stateful Anthropic stream converter."""

    def test_simple_text_response(self):
        """Test a simple text-only streaming response."""
        tracker = AnthropicStreamStateTracker()

        # First chunk with text content
        chunk1 = ModelResponse(
            id="msg_123",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(content="Hello", role="assistant"),
                    finish_reason=None,
                )
            ],
            model="claude-3-5-haiku-20241022",
        )

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
        tracker = AnthropicStreamStateTracker()

        # First chunk starts the block
        chunk1 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content="Hello", role="assistant"))],
            model="claude-3-5-haiku-20241022",
        )
        tracker.process_chunk(chunk1)

        # Second chunk should only emit delta
        chunk2 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content=" world"))],
            model="claude-3-5-haiku-20241022",
        )

        events2 = tracker.process_chunk(chunk2)

        assert len(events2) == 1
        assert events2[0]["type"] == "content_block_delta"
        assert events2[0]["index"] == 0
        assert events2[0]["delta"]["text"] == " world"

    def test_finish_with_message_delta(self):
        """Test that finish_reason emits message_delta and closes the block."""
        tracker = AnthropicStreamStateTracker()

        # Start with text
        chunk1 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content="Hi", role="assistant"))],
            model="claude-3-5-haiku-20241022",
        )
        tracker.process_chunk(chunk1)

        # Finish chunk with usage in _hidden_params
        chunk2 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(), finish_reason="stop")],
            model="claude-3-5-haiku-20241022",
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
        tracker = AnthropicStreamStateTracker()

        # Text content first
        chunk1 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content="Let me check", role="assistant"))],
            model="claude-3-5-haiku-20241022",
        )
        tracker.process_chunk(chunk1)

        # Tool call start (has id and name, empty args)
        from litellm.types.utils import ChatCompletionDeltaToolCall, Function

        chunk2 = ModelResponse(
            id="msg_123",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(
                        content="",
                        tool_calls=[
                            ChatCompletionDeltaToolCall(
                                id="toolu_123",
                                function=Function(name="get_weather", arguments=""),
                                type="function",
                                index=0,
                            )
                        ],
                    ),
                )
            ],
            model="claude-3-5-haiku-20241022",
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
        chunk3 = ModelResponse(
            id="msg_123",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(
                        content="",
                        tool_calls=[
                            ChatCompletionDeltaToolCall(
                                id=None,
                                function=Function(name=None, arguments=""),
                                type="function",
                                index=0,
                            )
                        ],
                    ),
                )
            ],
            model="claude-3-5-haiku-20241022",
        )

        events3 = tracker.process_chunk(chunk3)

        # Should emit empty input_json_delta
        assert len(events3) == 1
        assert events3[0]["type"] == "content_block_delta"
        assert events3[0]["index"] == 1
        assert events3[0]["delta"]["type"] == "input_json_delta"
        assert events3[0]["delta"]["partial_json"] == ""

        # Progressive argument chunks
        chunk4 = ModelResponse(
            id="msg_123",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(
                        content="",
                        tool_calls=[
                            ChatCompletionDeltaToolCall(
                                id=None,
                                function=Function(name=None, arguments='{"location"'),
                                type="function",
                                index=0,
                            )
                        ],
                    ),
                )
            ],
            model="claude-3-5-haiku-20241022",
        )

        events4 = tracker.process_chunk(chunk4)

        assert len(events4) == 1
        assert events4[0]["type"] == "content_block_delta"
        assert events4[0]["index"] == 1
        assert events4[0]["delta"]["partial_json"] == '{"location"'

    def test_complete_tool_call_buffered(self):
        """Test complete tool call in one chunk (from buffered policy)."""
        tracker = AnthropicStreamStateTracker()

        # Text first
        chunk1 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content="Sure", role="assistant"))],
            model="claude-3-5-haiku-20241022",
        )
        tracker.process_chunk(chunk1)

        # Complete tool call (has both id and args)
        from litellm.types.utils import ChatCompletionDeltaToolCall, Function

        chunk2 = ModelResponse(
            id="msg_123",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(
                        content="",
                        tool_calls=[
                            ChatCompletionDeltaToolCall(
                                id="toolu_456",
                                function=Function(name="search", arguments='{"query":"test"}'),
                                type="function",
                                index=0,
                            )
                        ],
                    ),
                )
            ],
            model="claude-3-5-haiku-20241022",
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

    def test_multiple_content_blocks(self):
        """Test that indices increment correctly for multiple blocks."""
        tracker = AnthropicStreamStateTracker()

        # Block 0: text
        chunk1 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content="First", role="assistant"))],
            model="claude-3-5-haiku-20241022",
        )
        events1 = tracker.process_chunk(chunk1)
        assert events1[0]["index"] == 0  # content_block_start

        # Block 1: tool (complete)
        from litellm.types.utils import ChatCompletionDeltaToolCall, Function

        chunk2 = ModelResponse(
            id="msg_123",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(
                        tool_calls=[
                            ChatCompletionDeltaToolCall(
                                id="tool_1",
                                function=Function(name="fn", arguments="{}"),
                                type="function",
                                index=0,
                            )
                        ]
                    ),
                )
            ],
            model="claude-3-5-haiku-20241022",
        )
        events2 = tracker.process_chunk(chunk2)
        # First event closes block 0, second starts block 1
        assert events2[0]["type"] == "content_block_stop"
        assert events2[0]["index"] == 0
        assert events2[1]["type"] == "content_block_start"
        assert events2[1]["index"] == 1

    def test_finish_reason_tool_use(self):
        """Test that finish_reason='tool_calls' maps to stop_reason='tool_use'."""
        tracker = AnthropicStreamStateTracker()

        # Start with text
        chunk1 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content="Hi", role="assistant"))],
            model="claude-3-5-haiku-20241022",
        )
        tracker.process_chunk(chunk1)

        # Finish with tool_calls
        chunk2 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(), finish_reason="tool_calls")],
            model="claude-3-5-haiku-20241022",
        )
        chunk2._hidden_params = {"usage": type("Usage", (), {"prompt_tokens": 5, "completion_tokens": 3})()}

        events2 = tracker.process_chunk(chunk2)

        assert events2[1]["type"] == "message_delta"
        assert events2[1]["delta"]["stop_reason"] == "tool_use"

    def test_empty_chunk_with_no_data(self):
        """Test that chunks with no meaningful data are handled gracefully."""
        tracker = AnthropicStreamStateTracker()

        # Start a block
        chunk1 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content="Hi", role="assistant"))],
            model="claude-3-5-haiku-20241022",
        )
        tracker.process_chunk(chunk1)

        # Empty chunk (no content, no tool_calls, no finish_reason)
        chunk2 = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content=""))],
            model="claude-3-5-haiku-20241022",
        )

        events2 = tracker.process_chunk(chunk2)

        # Should emit empty text_delta (fallback behavior)
        assert len(events2) == 1
        assert events2[0]["type"] == "content_block_delta"
        assert events2[0]["delta"]["type"] == "text_delta"
        assert events2[0]["delta"]["text"] == ""

    def test_unexpected_event_type_raises(self):
        """Test that unexpected event types from converter raise ValueError."""
        tracker = AnthropicStreamStateTracker()

        chunk = ModelResponse(
            id="msg_123",
            choices=[StreamingChoices(index=0, delta=Delta(content="Hi", role="assistant"))],
            model="claude-3-5-haiku-20241022",
        )

        # Mock the converter to return an unexpected event type
        # Patch it where it's imported in streaming_converters
        with patch(
            "luthien_proxy.v2.llm.streaming_converters.openai_chunk_to_anthropic_chunk",
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
                ModelResponse(
                    id="msg_1",
                    choices=[StreamingChoices(index=0, delta=Delta(content="A", role="assistant"))],
                    model="claude",
                ),
                ModelResponse(
                    id="msg_1",
                    choices=[StreamingChoices(index=0, delta=Delta(content="B"))],
                    model="claude",
                ),
            ],
            [
                ["content_block_start", "content_block_delta"],  # First chunk
                ["content_block_delta"],  # Second chunk
            ],
        ),
        # Text then finish
        (
            [
                ModelResponse(
                    id="msg_2",
                    choices=[StreamingChoices(index=0, delta=Delta(content="X", role="assistant"))],
                    model="claude",
                ),
                ModelResponse(
                    id="msg_2",
                    choices=[StreamingChoices(index=0, delta=Delta(), finish_reason="stop")],
                    model="claude",
                ),
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
    tracker = AnthropicStreamStateTracker()

    for i, chunk in enumerate(chunks):
        # Add usage to finish chunks if needed
        if chunk.choices[0].finish_reason:
            chunk._hidden_params = {"usage": type("Usage", (), {"prompt_tokens": 1, "completion_tokens": 1})()}

        events = tracker.process_chunk(chunk)
        actual_types = [e["type"] for e in events]

        assert actual_types == expected_event_types[i], f"Chunk {i} event types don't match"
