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
        self.current_block_type: str | None = None  # "thinking", "text", "tool_use"
        self.last_thinking_block_index: int | None = None  # Track for signature_delta
        self.thinking_block_needs_close: bool = False  # Delay close until signature

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

            # Check if chunk also has finish_reason and emit message_delta
            finish_reason = chunk.choices[0].finish_reason
            if finish_reason:
                stop_reason_map = {
                    "stop": "end_turn",
                    "tool_calls": "tool_use",
                    "length": "max_tokens",
                }
                events.append(
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": stop_reason_map.get(finish_reason, finish_reason),
                            "stop_sequence": None,
                        },
                        "usage": {"output_tokens": 0},
                    }
                )

            return events

        # Handle complete redacted thinking blocks
        if anthropic_event.get("_complete_redacted_thinking"):
            # Close pending thinking block if waiting for signature
            if self.thinking_block_needs_close and self.last_thinking_block_index is not None:
                events.append({"type": "content_block_stop", "index": self.last_thinking_block_index})
                self.thinking_block_needs_close = False

            # Close previous block if open
            if self.block_started:
                events.append({"type": "content_block_stop", "index": self.block_index})
                self.block_started = False
                self.block_index += 1

            # Emit start and stop for redacted thinking (no delta needed)
            events.append(
                {
                    "type": "content_block_start",
                    "index": self.block_index,
                    "content_block": anthropic_event["content_block"],
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

            # Start new block and track its type
            self.block_started = True
            content_block = anthropic_event.get("content_block", {})
            self.current_block_type = content_block.get("type", "text")
            anthropic_event["index"] = self.block_index
            events.append(anthropic_event)
            return events

        # Handle content_block_delta (thinking, text, or tool input)
        if event_type == "content_block_delta":
            delta_type = anthropic_event.get("delta", {}).get("type", "")

            # Special case: signature_delta should go to the LAST thinking block.
            # LiteLLM may deliver signatures AFTER text content starts, so we need to:
            # 1. Emit the signature to the thinking block
            # 2. Close the thinking block (if pending)
            # 3. Continue with text
            if delta_type == "signature_delta" and self.last_thinking_block_index is not None:
                anthropic_event["index"] = self.last_thinking_block_index
                events.append(anthropic_event)
                # Now close the thinking block if we were waiting for signature
                if self.thinking_block_needs_close:
                    events.append({"type": "content_block_stop", "index": self.last_thinking_block_index})
                    self.thinking_block_needs_close = False
                return events

            # Determine what block type this delta belongs to
            if delta_type == "thinking_delta":
                target_block_type = "thinking"
            elif delta_type == "text_delta":
                target_block_type = "text"
            else:
                target_block_type = "tool_use"

            # Handle transition between block types (thinking -> text)
            if self.block_started and self.current_block_type != target_block_type:
                # When transitioning FROM thinking, delay the close until we get signature
                if self.current_block_type == "thinking":
                    self.thinking_block_needs_close = True
                else:
                    # Close previous block immediately for non-thinking blocks
                    events.append({"type": "content_block_stop", "index": self.block_index})
                self.block_started = False
                self.block_index += 1

            # Start block if not already started
            if not self.block_started:
                self.block_started = True
                self.current_block_type = target_block_type

                if target_block_type == "thinking":
                    self.last_thinking_block_index = self.block_index
                    events.append(
                        {
                            "type": "content_block_start",
                            "index": self.block_index,
                            "content_block": {"type": "thinking", "thinking": ""},
                        }
                    )
                else:
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
            # Close any pending thinking block (if signature never arrived)
            if self.thinking_block_needs_close and self.last_thinking_block_index is not None:
                events.append({"type": "content_block_stop", "index": self.last_thinking_block_index})
                self.thinking_block_needs_close = False

            # Close current block before message_delta
            if self.block_started:
                events.append({"type": "content_block_stop", "index": self.block_index})
                self.block_started = False
                self.current_block_type = None

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

        # Handle thinking content (extended thinking / reasoning)
        # LiteLLM exposes this via delta.reasoning_content
        reasoning_content = getattr(delta, "reasoning_content", None)
        if reasoning_content is not None and reasoning_content != "":
            return {
                "type": "content_block_delta",
                "delta": {
                    "type": "thinking_delta",
                    "thinking": reasoning_content,
                },
            }

        # Handle thinking blocks with signature (for complete thinking data)
        # LiteLLM may provide thinking_blocks in streaming for signature delivery
        thinking_blocks = getattr(delta, "thinking_blocks", None)
        if thinking_blocks:
            for block in thinking_blocks:
                if isinstance(block, dict):
                    # Check for signature in thinking block
                    signature = block.get("signature")
                    if signature:
                        return {
                            "type": "content_block_delta",
                            "delta": {
                                "type": "signature_delta",
                                "signature": signature,
                            },
                        }
                    # Check for thinking content
                    thinking = block.get("thinking")
                    if thinking:
                        return {
                            "type": "content_block_delta",
                            "delta": {
                                "type": "thinking_delta",
                                "thinking": thinking,
                            },
                        }
                    # Handle redacted_thinking block
                    if block.get("type") == "redacted_thinking":
                        # Redacted thinking blocks are passed through as-is
                        # They don't have delta events, they're complete blocks
                        return {
                            "type": "content_block_start",
                            "content_block": {
                                "type": "redacted_thinking",
                                "data": block.get("data", ""),
                            },
                            "_complete_redacted_thinking": True,
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
