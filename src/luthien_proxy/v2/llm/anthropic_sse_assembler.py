# ABOUTME: Assembles Anthropic SSE events from OpenAI streaming chunks
# ABOUTME: Tracks content block state to generate proper event sequences with indices

"""Anthropic SSE stream assembler for OpenAI → Anthropic streaming conversion.

We convert all provider streams to OpenAI format (for policy consistency), but this
loses Anthropic-specific metadata like content block indices. Anthropic clients like Claude Code
require proper SSE format with sequential indices (0, 1, 2...) and explicit lifecycle events
(content_block_start → content_block_delta → content_block_stop) for streaming responses.

This module reconstructs the Anthropic format:
- `convert_chunk_to_event()` - Stateless OpenAI → Anthropic event conversion
- `process_chunk()` - Stateful assembly that tracks indices and manages block lifecycle
"""

from typing import cast

from litellm.types.utils import Delta, ModelResponse, StreamingChoices


class AnthropicSSEAssembler:
    """Assembles Anthropic SSE events from OpenAI streaming chunks.

    Maintains state across chunks to generate proper Anthropic SSE event sequences
    with correct content block indices and lifecycle events.
    """

    def __init__(self):
        """Initialize assembler with block index at 0."""
        self.block_started = False
        self.block_index = 0

    def process_chunk(self, chunk: ModelResponse) -> list[dict]:
        """Process OpenAI chunk and return list of Anthropic SSE events to emit.

        Args:
            chunk: OpenAI-format ModelResponse chunk from LiteLLM

        Returns:
            List of Anthropic SSE event dicts to emit

        Raises:
            ValueError: If chunk produces an unexpected event type
        """
        events: list[dict] = []

        # Convert OpenAI chunk to Anthropic event structure
        anthropic_event = self.convert_chunk_to_event(chunk)
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

    @staticmethod
    def convert_chunk_to_event(chunk: ModelResponse) -> dict:
        """Convert single OpenAI chunk to Anthropic SSE event structure.

        This is a stateless conversion - it doesn't track indices or block state,
        just converts the chunk structure to Anthropic's event format.

        Args:
            chunk: Streaming chunk from LiteLLM in OpenAI format

        Returns:
            Anthropic SSE event dict (may contain internal flags like _complete_tool_call)
        """
        delta: Delta = cast(StreamingChoices, chunk.choices[0]).delta

        # Handle tool calls
        if hasattr(delta, "tool_calls") and delta.tool_calls:
            tool_call = delta.tool_calls[0]  # Get first tool call
            has_id = hasattr(tool_call, "id") and tool_call.id
            has_args = (
                hasattr(tool_call, "function")
                and hasattr(tool_call.function, "arguments")
                and tool_call.function.arguments
            )

            # Complete tool call in one chunk (from buffered policy)
            # Send as content_block_start with the id and name
            if has_id and has_args:
                # Mark this chunk so gateway can emit additional events
                return {
                    "type": "content_block_start",
                    "index": getattr(tool_call, "index", 0),
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "input": {},
                    },
                    "_complete_tool_call": True,  # Internal flag for gateway
                    "_arguments": tool_call.function.arguments,
                }
            # Start of tool call (progressive streaming)
            elif has_id:
                return {
                    "type": "content_block_start",
                    "index": getattr(tool_call, "index", 0),
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.function.name if hasattr(tool_call, "function") else "",
                        "input": {},
                    },
                }
            # Delta for tool call arguments (progressive streaming)
            elif has_args:
                return {
                    "type": "content_block_delta",
                    "index": getattr(tool_call, "index", 0),
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": tool_call.function.arguments,
                    },
                }
            # Empty tool call chunk (no id, no args) - emit empty input_json_delta
            else:
                return {
                    "type": "content_block_delta",
                    "index": getattr(tool_call, "index", 0),
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": "",
                    },
                }

        # Handle text content
        content = delta.content or ""
        if content:
            return {
                "type": "content_block_delta",
                "delta": {
                    "type": "text_delta",
                    "text": content,
                },
            }

        # Handle finish reason (message_delta)
        finish_reason = chunk.choices[0].finish_reason
        if finish_reason:
            # Map OpenAI finish reasons to Anthropic stop reasons
            stop_reason_map = {
                "stop": "end_turn",
                "tool_calls": "tool_use",
                "length": "max_tokens",
            }

            # Extract usage from _hidden_params if available
            usage_dict = {}
            if hasattr(chunk, "_hidden_params") and "usage" in chunk._hidden_params:
                usage = chunk._hidden_params["usage"]
                usage_dict = {
                    "input_tokens": getattr(usage, "prompt_tokens", 0),
                    "output_tokens": getattr(usage, "completion_tokens", 0),
                }

            return {
                "type": "message_delta",
                "delta": {
                    "stop_reason": stop_reason_map.get(finish_reason, finish_reason),
                    "stop_sequence": None,
                },
                "usage": usage_dict if usage_dict else {"output_tokens": 0},
            }

        # Default: empty delta
        return {
            "type": "content_block_delta",
            "delta": {
                "type": "text_delta",
                "text": "",
            },
        }


__all__ = ["AnthropicSSEAssembler"]
