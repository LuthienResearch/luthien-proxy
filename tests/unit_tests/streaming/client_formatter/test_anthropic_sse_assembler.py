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
        """Test transition from thinking to text block."""
        assembler = AnthropicSSEAssembler()

        # First: thinking chunk
        events1 = assembler.process_chunk(create_chunk_with_reasoning("Thinking..."))
        assert events1[0]["content_block"]["type"] == "thinking"
        assert assembler.current_block_type == "thinking"
        assert assembler.block_index == 0

        # Second: text chunk should close thinking and start text
        events2 = assembler.process_chunk(create_chunk_with_text("Answer"))

        # Should have: content_block_stop, content_block_start, content_block_delta
        assert len(events2) == 3
        assert events2[0]["type"] == "content_block_stop"
        assert events2[0]["index"] == 0  # Closes thinking block at index 0
        assert events2[1]["type"] == "content_block_start"
        assert events2[1]["content_block"]["type"] == "text"
        assert events2[1]["index"] == 1  # Text block at index 1
        assert events2[2]["type"] == "content_block_delta"
        assert events2[2]["delta"]["type"] == "text_delta"

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
