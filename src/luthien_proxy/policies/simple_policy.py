"""SimplePolicy base class for content-level transformations.

SimplePolicy sacrifices streaming responsiveness for simpler policy authoring
by buffering streaming content and applying transformations when blocks complete.

Subclasses override simple methods to transform requests and responses:
- simple_on_request: transform request text
- simple_on_response_content: transform complete text content
- simple_on_anthropic_tool_call: transform complete Anthropic tool calls
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent
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
from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


@dataclass
class _BufferedAnthropicToolUse:
    id: str
    name: str
    input_json: str = ""


@dataclass
class _SimplePolicyAnthropicState:
    text_buffer: dict[int, str] = field(default_factory=dict)
    tool_buffer: dict[int, _BufferedAnthropicToolUse] = field(default_factory=dict)


class SimplePolicy(BasePolicy, AnthropicHookPolicy):
    """Convenience base class for content-level transformations.

    This class simplifies policy authoring by buffering streaming content, effectively trading
    off streaming responsiveness for ease of implementation. To implement a SimplePolicy, you
    only need to implement three simple methods:
    - simple_on_request (request str->str)
    - simple_on_response_content (complete content str->str)
    - simple_on_anthropic_tool_call (complete Anthropic tool call -> tool call)

    You still have access to PolicyContext for observability, request state, etc, enabling you to
    do everything a full PolicyProtocol implementation can do, just with less complexity (and no
    streaming responsiveness).
    """

    def _anthropic_state(self, context: "PolicyContext") -> _SimplePolicyAnthropicState:
        """Get or create typed request-scoped Anthropic state."""
        return context.get_request_state(self, _SimplePolicyAnthropicState, _SimplePolicyAnthropicState)

    # ===== Simple methods that subclasses override =====

    async def simple_on_request(self, request_str: str, context: PolicyContext) -> str:
        """Transform request string. Override to implement request transformations.

        Args:
            request_str (str): The original request as a string
            context (PolicyContext): Policy context (includes observability, request state)
        """
        return request_str

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Transform complete response content. Override to implement content transformations.

        Args:
            content (str): Complete response content ("Hello user")
            context (PolicyContext): Policy context (includes request, response metadata, observability, request state)

        """
        return content

    async def simple_on_anthropic_tool_call(
        self, tool_call: AnthropicToolUseBlock, context: PolicyContext
    ) -> AnthropicToolUseBlock:
        """Transform/validate a complete Anthropic tool call. Override for Anthropic-specific handling.

        By default, this delegates to simple_on_response_tool_call with format conversion.
        Override this method if you need Anthropic-specific tool call handling.

        Args:
            tool_call: The complete Anthropic tool_use block
            context: Policy context (includes request, response metadata, observability, request state)

        Returns:
            Transformed tool_use block
        """
        return tool_call

    async def on_anthropic_streaming_policy_complete(self, context: PolicyContext) -> None:
        """Clear request-scoped Anthropic buffers."""
        context.pop_request_state(self, _SimplePolicyAnthropicState)

    # ===== Anthropic hooks (via AnthropicHookPolicy) =====

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        """Process request by extracting last message text and calling simple_on_request.

        Args:
            request: The Anthropic Messages API request
            context: Policy context (includes observability, request state)

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

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        """Process non-streaming response through simple hooks.

        Iterates through content blocks and applies:
        - simple_on_response_content for text blocks
        - simple_on_anthropic_tool_call for tool_use blocks

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
                block_id = block.get("id")
                block_name = block.get("name")
                if block_id is None or block_name is None:
                    raise ValueError(
                        f"Malformed tool_use block: missing required field(s) (id={block_id!r}, name={block_name!r})"
                    )
                tool_block: AnthropicToolUseBlock = {
                    "type": "tool_use",
                    "id": block_id,
                    "name": block_name,
                    "input": block.get("input", {}),
                }
                transformed = await self.simple_on_anthropic_tool_call(tool_block, context)
                content_blocks[i] = transformed

        return response

    # ===== Anthropic streaming hook =====

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        """Process streaming events with buffering for content transformation.

        Buffers content_block_delta events and emits transformed content on
        content_block_stop. Other events pass through unchanged.

        Args:
            event: The Anthropic streaming event
            context: Policy context

        Returns:
            List of events to emit (possibly transformed), or empty list to filter
        """
        # Content block start: initialize buffer
        if isinstance(event, RawContentBlockStartEvent):
            index = event.index
            state = self._anthropic_state(context)
            content_block = event.content_block

            # Initialize appropriate buffer based on block type
            if hasattr(content_block, "type"):
                if content_block.type == "text":
                    state.text_buffer[index] = ""
                elif content_block.type == "tool_use":
                    # Initialize tool buffer with block info from SDK ToolUseBlock
                    if isinstance(content_block, ToolUseBlock):
                        state.tool_buffer[index] = _BufferedAnthropicToolUse(
                            id=content_block.id,
                            name=content_block.name,
                        )

            return [event]

        # Content block delta: accumulate in buffer, suppress output
        if isinstance(event, RawContentBlockDeltaEvent):
            index = event.index
            state = self._anthropic_state(context)
            delta = event.delta

            if isinstance(delta, TextDelta):
                # Accumulate text delta
                if index not in state.text_buffer:
                    raise RuntimeError(
                        f"Received TextDelta for index {index} but no buffer exists. "
                        "This indicates a missing content_block_start event."
                    )
                state.text_buffer[index] += delta.text
                # Suppress the delta - we'll emit on stop
                return []

            if isinstance(delta, InputJSONDelta):
                # Accumulate JSON delta for tool calls
                if index not in state.tool_buffer:
                    raise RuntimeError(
                        f"Received InputJSONDelta for index {index} but no buffer exists. "
                        "This indicates a missing content_block_start event."
                    )
                state.tool_buffer[index].input_json += delta.partial_json
                # Suppress the delta - we'll emit on stop
                return []

            # Other delta types (thinking, etc.) pass through unchanged
            return [event]

        # Content block stop: emit transformed content
        if isinstance(event, RawContentBlockStopEvent):
            index = event.index
            state = self._anthropic_state(context)

            # Handle text block completion
            if index in state.text_buffer:
                content = state.text_buffer.pop(index)
                transformed = await self.simple_on_response_content(content, context)

                # Emit the complete transformed text as a single delta before the stop
                text_delta = TextDelta.model_construct(type="text_delta", text=transformed)
                delta_event = RawContentBlockDeltaEvent.model_construct(
                    type="content_block_delta",
                    index=index,
                    delta=text_delta,
                )
                context.record_event(
                    "policy.simple_policy.emit_transformed",
                    {"index": index, "content_length": len(transformed)},
                )
                return [delta_event, event]

            # Handle tool call completion
            if index in state.tool_buffer:
                tool_info = state.tool_buffer.pop(index)

                # Parse the accumulated JSON - empty string means no input
                input_data = json.loads(tool_info.input_json) if tool_info.input_json else {}

                tool_block: AnthropicToolUseBlock = {
                    "type": "tool_use",
                    "id": tool_info.id,
                    "name": tool_info.name,
                    "input": input_data,
                }

                transformed = await self.simple_on_anthropic_tool_call(tool_block, context)

                # Emit the complete transformed tool call as JSON delta before stop
                transformed_json = json.dumps(transformed["input"])
                json_delta = InputJSONDelta.model_construct(type="input_json_delta", partial_json=transformed_json)
                delta_event = RawContentBlockDeltaEvent.model_construct(
                    type="content_block_delta",
                    index=index,
                    delta=json_delta,
                )
                context.record_event(
                    "policy.simple_policy.emit_transformed_tool",
                    {"index": index, "tool_name": transformed["name"]},
                )
                return [delta_event, event]

            # No buffered content for this index, just pass through the stop
            return [event]

        # All other events (message_start, message_delta, message_stop) pass through
        return [event]


__all__ = ["SimplePolicy"]
