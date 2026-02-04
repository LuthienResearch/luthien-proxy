# ABOUTME: SimplePolicy base class for Anthropic-native content-level transformations
"""SimplePolicy base class for Anthropic-native content-level transformations.

AnthropicSimplePolicy sacrifices streaming responsiveness for simpler policy authoring
by buffering streaming content and applying transformations when blocks complete.

This is the Anthropic-native equivalent of the OpenAI-based SimplePolicy, working
directly with Anthropic API types (AnthropicRequest, AnthropicResponse, and
streaming events like RawContentBlockDeltaEvent).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextDelta,
    ToolUseBlock,
)

from luthien_proxy.llm.types.anthropic import (
    AnthropicToolUseBlock,
)
from luthien_proxy.policy_core.anthropic_protocol import AnthropicStreamEvent

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


class AnthropicSimplePolicy:
    """Convenience base class for Anthropic-native content-level transformations.

    This class simplifies policy authoring by providing hooks that work with complete
    content rather than individual streaming deltas. Subclasses override three methods:
    - simple_on_request: transform the last user message text
    - simple_on_response_content: transform complete text content
    - simple_on_response_tool_call: transform complete tool calls

    For streaming, this policy buffers deltas and emits transformed content when
    content blocks complete. This trades streaming responsiveness for simplicity.

    Note on streaming:
        Unlike OpenAI's streaming which uses a separate StreamingPolicyContext,
        Anthropic streaming uses on_stream_event with individual events. This class
        buffers content_block_delta events and emits transformed content only on
        content_block_stop events. Message-level events (message_start, message_delta,
        message_stop) pass through unchanged.
    """

    def __init__(self) -> None:
        """Initialize the policy with empty stream buffers."""
        # Buffer for streaming content: maps block index to accumulated content
        self._text_buffer: dict[int, str] = {}
        self._tool_buffer: dict[int, dict] = {}
        self._pending_stop_event: RawContentBlockStopEvent | None = None

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy. Defaults to class name."""
        return self.__class__.__name__

    # ===== Simple methods that subclasses override =====

    async def simple_on_request(self, request_text: str, context: PolicyContext) -> str:
        """Transform request text. Override to implement request transformations.

        Args:
            request_text: The text content from the last user message
            context: Policy context (includes observability, scratchpad)

        Returns:
            Transformed text to use in the request
        """
        return request_text

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Transform complete response text content. Override to implement transformations.

        Args:
            content: Complete text content from a text block
            context: Policy context (includes request, response metadata, observability, scratchpad)

        Returns:
            Transformed text content
        """
        return content

    async def simple_on_response_tool_call(
        self, tool_call: AnthropicToolUseBlock, context: PolicyContext
    ) -> AnthropicToolUseBlock:
        """Transform/validate a complete tool call. Override to implement tool call transformations.

        Args:
            tool_call: The complete Anthropic tool_use block
            context: Policy context (includes request, response metadata, observability, scratchpad)

        Returns:
            Transformed tool_use block
        """
        return tool_call

    # ===== Implementation of non-streaming hooks =====

    async def on_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        """Process request by extracting last message text and calling simple_on_request.

        Args:
            request: The Anthropic Messages API request
            context: Policy context (includes observability, scratchpad)

        Returns:
            Request with potentially modified last message content
        """
        messages = request.get("messages", [])
        if not messages:
            return request

        last_message = messages[-1]
        content = last_message.get("content")

        if isinstance(content, str):
            transformed = await self.simple_on_request(content, context)
            last_message["content"] = transformed
        elif isinstance(content, list):
            # Find and transform text blocks in the content list
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                    text = block.get("text")
                    if isinstance(text, str):
                        block["text"] = await self.simple_on_request(text, context)

        return request

    async def on_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        """Process non-streaming response through simple hooks.

        Iterates through content blocks and applies:
        - simple_on_response_content for text blocks
        - simple_on_response_tool_call for tool_use blocks

        Args:
            response: The Anthropic Messages API response
            context: Policy context

        Returns:
            Response with transformed content
        """
        content_blocks = response.get("content", [])

        for i, block in enumerate(content_blocks):
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")

            if block_type == "text" and "text" in block:
                text = block.get("text")
                if isinstance(text, str):
                    block["text"] = await self.simple_on_response_content(text, context)

            elif block_type == "tool_use":
                # Cast to AnthropicToolUseBlock for type safety
                tool_block: AnthropicToolUseBlock = {
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                }
                transformed = await self.simple_on_response_tool_call(tool_block, context)
                content_blocks[i] = transformed

        return response

    # ===== Implementation of streaming hook =====

    async def on_stream_event(self, event: AnthropicStreamEvent, context: PolicyContext) -> AnthropicStreamEvent | None:
        """Process streaming events with buffering for content transformation.

        Buffers content_block_delta events and emits transformed content on
        content_block_stop. Other events pass through unchanged.

        Args:
            event: The Anthropic streaming event
            context: Policy context

        Returns:
            Event to emit (possibly transformed), or None to filter
        """
        # Content block start: initialize buffer
        if isinstance(event, RawContentBlockStartEvent):
            index = event.index
            content_block = event.content_block

            # Initialize appropriate buffer based on block type
            if hasattr(content_block, "type"):
                if content_block.type == "text":
                    self._text_buffer[index] = ""
                elif content_block.type == "tool_use":
                    # Initialize tool buffer with block info from SDK ToolUseBlock
                    if isinstance(content_block, ToolUseBlock):
                        self._tool_buffer[index] = {
                            "id": content_block.id,
                            "name": content_block.name,
                            "input_json": "",
                        }

            return event

        # Content block delta: accumulate in buffer, suppress output
        if isinstance(event, RawContentBlockDeltaEvent):
            index = event.index
            delta = event.delta

            if isinstance(delta, TextDelta):
                # Accumulate text delta
                if index in self._text_buffer:
                    self._text_buffer[index] += delta.text
                # Suppress the delta - we'll emit on stop
                return None

            if isinstance(delta, InputJSONDelta):
                # Accumulate JSON delta for tool calls
                if index in self._tool_buffer:
                    self._tool_buffer[index]["input_json"] += delta.partial_json
                # Suppress the delta - we'll emit on stop
                return None

            # Other delta types (thinking, etc.) pass through unchanged
            return event

        # Content block stop: emit transformed content
        if isinstance(event, RawContentBlockStopEvent):
            index = event.index

            # Handle text block completion
            if index in self._text_buffer:
                content = self._text_buffer.pop(index)
                transformed = await self.simple_on_response_content(content, context)

                # Emit the complete transformed text as a single delta before the stop
                text_delta = TextDelta.model_construct(type="text_delta", text=transformed)
                delta_event = RawContentBlockDeltaEvent.model_construct(
                    type="content_block_delta",
                    index=index,
                    delta=text_delta,
                )
                # Record that we need to emit both the delta and the stop
                context.record_event(
                    "policy.anthropic_simple_policy.emit_transformed",
                    {"index": index, "content_length": len(transformed)},
                )
                # Return the delta event; the orchestrator should handle emitting stop after
                # Since we can only return one event, we'll need to handle this differently
                # For now, return the stop event and let the buffer clearing happen
                # The transformed content needs to be emitted via a different mechanism

                # Actually, we need to return the transformed delta first, then the stop
                # But on_stream_event can only return one event. We'll store the pending stop.
                self._pending_stop_event = event
                return delta_event

            # Handle tool call completion
            if index in self._tool_buffer:
                tool_info = self._tool_buffer.pop(index)

                # Parse the accumulated JSON
                try:
                    input_data = json.loads(tool_info["input_json"]) if tool_info["input_json"] else {}
                except json.JSONDecodeError:
                    input_data = {}

                tool_block: AnthropicToolUseBlock = {
                    "type": "tool_use",
                    "id": tool_info["id"],
                    "name": tool_info["name"],
                    "input": input_data,
                }

                transformed = await self.simple_on_response_tool_call(tool_block, context)

                # Emit the complete transformed tool call as JSON delta before stop
                transformed_json = json.dumps(transformed["input"])
                json_delta = InputJSONDelta.model_construct(type="input_json_delta", partial_json=transformed_json)
                delta_event = RawContentBlockDeltaEvent.model_construct(
                    type="content_block_delta",
                    index=index,
                    delta=json_delta,
                )
                context.record_event(
                    "policy.anthropic_simple_policy.emit_transformed_tool",
                    {"index": index, "tool_name": transformed["name"]},
                )
                # Same issue as above - we need to emit delta then stop
                self._pending_stop_event = event
                return delta_event

            # No buffered content for this index, just pass through the stop
            return event

        # All other events (message_start, message_delta, message_stop) pass through
        return event

    def get_pending_stop_event(self) -> RawContentBlockStopEvent | None:
        """Get and clear any pending stop event that needs to be emitted after a transformed delta.

        This is used by the orchestrator to emit the stop event after the transformed content.

        Returns:
            The pending stop event if one exists, None otherwise
        """
        event = getattr(self, "_pending_stop_event", None)
        self._pending_stop_event = None
        return event

    def clear_buffers(self) -> None:
        """Clear all streaming buffers. Call this between requests."""
        self._text_buffer.clear()
        self._tool_buffer.clear()
        self._pending_stop_event = None


__all__ = ["AnthropicSimplePolicy"]
