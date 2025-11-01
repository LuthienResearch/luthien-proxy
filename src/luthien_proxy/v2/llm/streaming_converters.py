# ABOUTME: Stateful streaming format converters for OpenAI → Anthropic conversion
# ABOUTME: Tracks content block index to generate proper Anthropic SSE events

"""Stateful streaming converters between OpenAI and Anthropic formats.

This module handles the conversion of OpenAI-style streaming chunks into
Anthropic's stateful SSE format, which requires explicit content_block_start,
content_block_delta, and content_block_stop events with sequential indices.
"""

from __future__ import annotations

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.llm.format_converters import openai_chunk_to_anthropic_chunk


class AnthropicStreamStateTracker:
    """Tracks content block index for converting OpenAI chunks to Anthropic SSE events.

    Anthropic's streaming format requires:
    1. content_block_start event before any deltas (with index and content_block metadata)
    2. content_block_delta events with index field
    3. content_block_stop event when block completes (with index)

    This tracker maintains minimal state (just the current block index) across chunks.
    """

    def __init__(self):
        """Initialize tracker with block index at 0."""
        self.block_started = False
        self.block_index = 0

    def process_chunk(self, chunk: ModelResponse) -> list[dict]:
        """Process OpenAI chunk and return list of Anthropic SSE events to emit.

        Args:
            chunk: OpenAI-format ModelResponse chunk

        Returns:
            List of Anthropic event dicts to emit as SSE events

        Raises:
            AssertionError: If chunk structure violates assumptions
        """
        events: list[dict] = []

        # Convert OpenAI chunk to Anthropic format
        anthropic_event = openai_chunk_to_anthropic_chunk(chunk)
        assert isinstance(anthropic_event, dict), f"Expected dict from converter, got {type(anthropic_event)}"

        event_type = anthropic_event.get("type")

        # Handle complete tool calls (buffered by policy)
        if anthropic_event.get("_complete_tool_call"):
            # Close previous block if open
            if self.block_started:
                events.append({"type": "content_block_stop", "index": self.block_index})
                self.block_started = False
                self.block_index += 1

            # Emit start, delta, and stop for complete tool call
            events.append(
                {
                    "type": "content_block_start",
                    "index": self.block_index,
                    "content_block": anthropic_event["content_block"],
                }
            )
            events.append(
                {
                    "type": "content_block_delta",
                    "index": self.block_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": anthropic_event["_arguments"],
                    },
                }
            )
            events.append({"type": "content_block_stop", "index": self.block_index})
            self.block_index += 1
            return events

        # Handle explicit content_block_start from converter
        if event_type == "content_block_start":
            # Close previous block if open
            if self.block_started:
                events.append({"type": "content_block_stop", "index": self.block_index})
                self.block_index += 1

            # Start new block
            self.block_started = True
            anthropic_event["index"] = self.block_index
            events.append(anthropic_event)
            return events

        # Handle content_block_delta (text or tool input)
        if event_type == "content_block_delta":
            # Start block if not already started (text content case)
            if not self.block_started:
                self.block_started = True
                events.append(
                    {
                        "type": "content_block_start",
                        "index": self.block_index,
                        "content_block": {"type": "text", "text": ""},
                    }
                )

            # Add index to delta and emit
            anthropic_event["index"] = self.block_index
            events.append(anthropic_event)
            return events

        # Handle message_delta (finish reason)
        if event_type == "message_delta":
            # Close block before message_delta
            if self.block_started:
                events.append({"type": "content_block_stop", "index": self.block_index})
                self.block_started = False

            events.append(anthropic_event)
            return events

        # Unknown event type - fail loudly
        raise ValueError(f"Unexpected event type from converter: {event_type}, event: {anthropic_event}")


__all__ = ["AnthropicStreamStateTracker"]
