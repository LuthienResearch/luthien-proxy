# ABOUTME: Unit tests for AnthropicSSEAssembler
# ABOUTME: Tests thinking block handling, block transitions, and event generation

"""Tests for AnthropicSSEAssembler thinking block handling."""

from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.streaming.client_formatter.anthropic_sse_assembler import (
    AnthropicSSEAssembler,
)


def create_chunk_with_reasoning(reasoning: str, finish_reason: str | None = None) -> ModelResponse:
    """Create a ModelResponse chunk with reasoning_content."""
    delta = Delta(content=None, role="assistant")
    delta.reasoning_content = reasoning
    return ModelResponse(
        id="chatcmpl-123",
        choices=[
            StreamingChoices(
                delta=delta,
                finish_reason=finish_reason,
                index=0,
            )
        ],
        created=1234567890,
        model="claude-sonnet-4-5-20250514",
        object="chat.completion.chunk",
    )


def create_chunk_with_text(text: str, finish_reason: str | None = None) -> ModelResponse:
    """Create a ModelResponse chunk with text content."""
    return ModelResponse(
        id="chatcmpl-123",
        choices=[
            StreamingChoices(
                delta=Delta(content=text, role="assistant"),
                finish_reason=finish_reason,
                index=0,
            )
        ],
        created=1234567890,
        model="claude-sonnet-4-5-20250514",
        object="chat.completion.chunk",
    )


def create_chunk_with_thinking_blocks(blocks: list[dict], finish_reason: str | None = None) -> ModelResponse:
    """Create a ModelResponse chunk with thinking_blocks attribute."""
    delta = Delta(content=None, role="assistant")
    delta.thinking_blocks = blocks
    return ModelResponse(
        id="chatcmpl-123",
        choices=[
            StreamingChoices(
                delta=delta,
                finish_reason=finish_reason,
                index=0,
            )
        ],
        created=1234567890,
        model="claude-sonnet-4-5-20250514",
        object="chat.completion.chunk",
    )


class TestConvertChunkToEvent:
    """Test convert_chunk_to_event method."""

    def test_reasoning_content_becomes_thinking_delta(self):
        """Test that reasoning_content is converted to thinking_delta."""
        assembler = AnthropicSSEAssembler()
        chunk = create_chunk_with_reasoning("Let me think step by step...")

        event = assembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "thinking_delta"
        assert event["delta"]["thinking"] == "Let me think step by step..."

    def test_text_content_becomes_text_delta(self):
        """Test that text content is converted to text_delta."""
        assembler = AnthropicSSEAssembler()
        chunk = create_chunk_with_text("The answer is 42")

        event = assembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "text_delta"
        assert event["delta"]["text"] == "The answer is 42"

    def test_thinking_blocks_with_signature(self):
        """Test that thinking_blocks with signature become signature_delta."""
        assembler = AnthropicSSEAssembler()
        chunk = create_chunk_with_thinking_blocks([{"type": "thinking", "signature": "sig_abc123"}])

        event = assembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "signature_delta"
        assert event["delta"]["signature"] == "sig_abc123"

    def test_thinking_blocks_with_thinking_content(self):
        """Test that thinking_blocks with thinking become thinking_delta."""
        assembler = AnthropicSSEAssembler()
        chunk = create_chunk_with_thinking_blocks([{"type": "thinking", "thinking": "Internal reasoning..."}])

        event = assembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "thinking_delta"
        assert event["delta"]["thinking"] == "Internal reasoning..."

    def test_redacted_thinking_block(self):
        """Test that redacted_thinking blocks are handled correctly."""
        assembler = AnthropicSSEAssembler()
        chunk = create_chunk_with_thinking_blocks([{"type": "redacted_thinking", "data": "encrypted_data_xyz"}])

        event = assembler.convert_chunk_to_event(chunk)

        assert event["type"] == "content_block_start"
        assert event["content_block"]["type"] == "redacted_thinking"
        assert event["content_block"]["data"] == "encrypted_data_xyz"
        assert event["_complete_redacted_thinking"] is True


class TestProcessChunk:
    """Test process_chunk method with thinking blocks."""

    def test_first_thinking_chunk_starts_thinking_block(self):
        """Test that first thinking chunk starts a thinking block."""
        assembler = AnthropicSSEAssembler()
        chunk = create_chunk_with_reasoning("Thinking...")

        events = assembler.process_chunk(chunk)

        # Should have: content_block_start, content_block_delta
        assert len(events) == 2
        assert events[0]["type"] == "content_block_start"
        assert events[0]["content_block"]["type"] == "thinking"
        assert events[0]["index"] == 0
        assert events[1]["type"] == "content_block_delta"
        assert events[1]["delta"]["type"] == "thinking_delta"

    def test_thinking_to_text_transition(self):
        """Test transition from thinking to text block.

        Thinking block close is DELAYED until signature arrives (LiteLLM sends
        signatures after text content starts).
        """
        assembler = AnthropicSSEAssembler()

        # First: thinking chunk
        events1 = assembler.process_chunk(create_chunk_with_reasoning("Thinking..."))
        assert events1[0]["content_block"]["type"] == "thinking"
        assert assembler.current_block_type == "thinking"
        assert assembler.block_index == 0

        # Second: text chunk should start text but NOT close thinking yet
        events2 = assembler.process_chunk(create_chunk_with_text("Answer"))

        # Should have: content_block_start (text), content_block_delta (text)
        # Thinking block close is delayed until signature arrives
        assert len(events2) == 2
        assert events2[0]["type"] == "content_block_start"
        assert events2[0]["content_block"]["type"] == "text"
        assert events2[0]["index"] == 1  # Text block at index 1
        assert events2[1]["type"] == "content_block_delta"
        assert events2[1]["delta"]["type"] == "text_delta"

        # Thinking block close is pending
        assert assembler.thinking_block_needs_close is True

        # Third: signature arrives and closes the thinking block
        events3 = assembler.process_chunk(
            create_chunk_with_thinking_blocks([{"type": "thinking", "signature": "sig_xyz"}])
        )

        assert len(events3) == 2
        assert events3[0]["type"] == "content_block_delta"
        assert events3[0]["delta"]["type"] == "signature_delta"
        assert events3[0]["index"] == 0  # Goes to thinking block
        assert events3[1]["type"] == "content_block_stop"
        assert events3[1]["index"] == 0  # Closes thinking block

        # Pending close is resolved
        assert assembler.thinking_block_needs_close is False

    def test_multiple_thinking_deltas_stay_in_same_block(self):
        """Test that consecutive thinking deltas stay in same block."""
        assembler = AnthropicSSEAssembler()

        # First thinking chunk
        events1 = assembler.process_chunk(create_chunk_with_reasoning("Step 1..."))
        assert len(events1) == 2  # start + delta

        # Second thinking chunk - should NOT start new block
        events2 = assembler.process_chunk(create_chunk_with_reasoning("Step 2..."))
        assert len(events2) == 1  # just delta
        assert events2[0]["type"] == "content_block_delta"
        assert events2[0]["index"] == 0  # Still at index 0

        # Block should still be open
        assert assembler.block_started is True
        assert assembler.current_block_type == "thinking"

    def test_signature_delta_stays_in_thinking_block(self):
        """Test that signature_delta stays in the same thinking block."""
        assembler = AnthropicSSEAssembler()

        # Start with thinking
        assembler.process_chunk(create_chunk_with_reasoning("Reasoning..."))
        assert assembler.current_block_type == "thinking"

        # Signature should stay in thinking block
        events = assembler.process_chunk(
            create_chunk_with_thinking_blocks([{"type": "thinking", "signature": "sig_abc"}])
        )

        assert len(events) == 1
        assert events[0]["type"] == "content_block_delta"
        assert events[0]["delta"]["type"] == "signature_delta"
        assert events[0]["index"] == 0  # Still in thinking block

    def test_redacted_thinking_closes_previous_block(self):
        """Test that redacted_thinking block closes any open block."""
        assembler = AnthropicSSEAssembler()

        # Start with regular thinking
        assembler.process_chunk(create_chunk_with_reasoning("Initial thought..."))
        assert assembler.block_index == 0

        # Redacted thinking should close previous and emit as complete block
        events = assembler.process_chunk(
            create_chunk_with_thinking_blocks([{"type": "redacted_thinking", "data": "encrypted"}])
        )

        # Should have: content_block_stop, content_block_start (redacted), content_block_stop
        assert len(events) == 3
        assert events[0]["type"] == "content_block_stop"
        assert events[0]["index"] == 0
        assert events[1]["type"] == "content_block_start"
        assert events[1]["content_block"]["type"] == "redacted_thinking"
        assert events[1]["index"] == 1
        assert events[2]["type"] == "content_block_stop"
        assert events[2]["index"] == 1

    def test_text_only_response_unchanged(self):
        """Test that text-only responses work as before."""
        assembler = AnthropicSSEAssembler()

        # Just text - no thinking
        events = assembler.process_chunk(create_chunk_with_text("Hello world"))

        assert len(events) == 2
        assert events[0]["type"] == "content_block_start"
        assert events[0]["content_block"]["type"] == "text"
        assert events[1]["type"] == "content_block_delta"
        assert events[1]["delta"]["type"] == "text_delta"

    def test_thinking_to_tool_call_transition(self):
        """Test transition from thinking to tool_use block.

        This is the most complex event sequencing - thinking → tool_use.
        Tool calls close the thinking block immediately (no delayed close).
        """
        assembler = AnthropicSSEAssembler()

        # Start with thinking
        events1 = assembler.process_chunk(create_chunk_with_reasoning("Let me use a tool..."))
        assert events1[0]["content_block"]["type"] == "thinking"
        assert assembler.current_block_type == "thinking"

        # Create a complete tool call chunk (id + arguments)
        delta = Delta(content=None, role="assistant")
        tool_call = type(
            "ToolCall",
            (),
            {
                "id": "call_123",
                "index": 0,
                "function": type("Function", (), {"name": "read_file", "arguments": '{"path": "test.txt"}'})(),
            },
        )()
        delta.tool_calls = [tool_call]
        tool_chunk = ModelResponse(
            id="chatcmpl-123",
            choices=[StreamingChoices(delta=delta, finish_reason=None, index=0)],
            created=1234567890,
            model="claude-sonnet-4-5-20250514",
            object="chat.completion.chunk",
        )

        # Tool call should close thinking and start tool_use
        events2 = assembler.process_chunk(tool_chunk)

        # Should have: content_block_stop (thinking), content_block_start (tool_use),
        #              content_block_delta (input_json), content_block_stop (tool_use)
        event_types = [e["type"] for e in events2]
        assert "content_block_stop" in event_types, f"Got: {event_types}"
        assert "content_block_start" in event_types

        # Find tool_use start
        tool_starts = [e for e in events2 if e.get("content_block", {}).get("type") == "tool_use"]
        assert len(tool_starts) == 1
        assert tool_starts[0]["content_block"]["name"] == "read_file"

    def test_finish_reason_closes_block(self):
        """Test that finish_reason properly closes open block."""
        assembler = AnthropicSSEAssembler()

        # Thinking chunk
        assembler.process_chunk(create_chunk_with_reasoning("Done thinking"))

        # Finish chunk
        chunk = create_chunk_with_text("", finish_reason="stop")
        events = assembler.process_chunk(chunk)

        # Should close block and emit message_delta
        assert any(e["type"] == "content_block_stop" for e in events)
        assert any(e["type"] == "message_delta" for e in events)
