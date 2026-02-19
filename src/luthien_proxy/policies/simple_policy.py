# ABOUTME: SimplePolicy base class for content-level transformations (supports both OpenAI and Anthropic)
"""SimplePolicy base class for content-level transformations.

SimplePolicy sacrifices streaming responsiveness for simpler policy authoring
by buffering streaming content and applying transformations when blocks complete.

This unified class supports both OpenAI and Anthropic API formats. Subclasses
override three simple methods:
- simple_on_request: transform request text
- simple_on_response_content: transform complete text content
- simple_on_response_tool_call: transform complete tool calls
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
from litellm.types.utils import Choices

from luthien_proxy.llm.types.anthropic import (
    AnthropicToolUseBlock,
)
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
    BasePolicy,
    OpenAIPolicyInterface,
    create_finish_chunk,
)
from luthien_proxy.policy_core.streaming_utils import (
    get_last_ingress_chunk,
    send_chunk,
    send_text,
    send_tool_call,
)
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock

if TYPE_CHECKING:
    from litellm.types.utils import ChatCompletionMessageToolCall, ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )

logger = logging.getLogger(__name__)


class SimplePolicy(BasePolicy, OpenAIPolicyInterface, AnthropicPolicyInterface):
    """Convenience base class for content-level transformations.

    This class simplifies policy authoring by buffering streaming content, effectively trading
    off streaming responsiveness for ease of implementation. To implement a SimplePolicy, you
    only need to implement three simple methods:
    - simple_on_request (request str->str)
    - simple_on_response_content (complete content str->str)
    - simple_on_response_tool_call (complete tool call -> tool call)

    You still have access to PolicyContext for observability, scratchpad, etc, enabling you to
    do everything a full PolicyProtocol implementation can do, just with less complexity (and no
    streaming responsiveness).

    This unified class supports both OpenAI and Anthropic API formats. The same simple_*
    methods are called for both formats, with appropriate type conversions handled internally.
    """

    def __init__(self) -> None:
        """Initialize the policy with empty stream buffers for Anthropic streaming."""
        # Buffer for Anthropic streaming content: maps block index to accumulated content
        self._text_buffer: dict[int, str] = {}
        self._tool_buffer: dict[int, dict] = {}

    # ===== Simple methods that subclasses override =====

    async def simple_on_request(self, request_str: str, context: PolicyContext) -> str:
        """Transform request string. Override to implement request transformations.

        Args:
            request_str (str): The original request as a string
            context (PolicyContext): Policy context (includes observability, scratchpad)
        """
        return request_str

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Transform complete response content. Override to implement content transformations.

        Args:
            content (str): Complete response content ("Hello user")
            context (PolicyContext): Policy context (includes request, response metadata, observability, scratchpad)

        """
        return content

    async def simple_on_response_tool_call(
        self, tool_call: ChatCompletionMessageToolCall, context: PolicyContext
    ) -> ChatCompletionMessageToolCall:
        """Transform/validate a complete tool call. Override to implement tool call transformations.

        Args:
            tool_call (ChatCompletionMessageToolCall): The complete tool call
            context (PolicyContext): Policy context (includes request, response metadata, observability, scratchpad)
        """
        return tool_call

    async def simple_on_anthropic_tool_call(
        self, tool_call: AnthropicToolUseBlock, context: PolicyContext
    ) -> AnthropicToolUseBlock:
        """Transform/validate a complete Anthropic tool call. Override for Anthropic-specific handling.

        By default, this delegates to simple_on_response_tool_call with format conversion.
        Override this method if you need Anthropic-specific tool call handling.

        Args:
            tool_call: The complete Anthropic tool_use block
            context: Policy context (includes request, response metadata, observability, scratchpad)

        Returns:
            Transformed tool_use block
        """
        return tool_call

    # ===== OpenAI non-streaming hooks =====

    async def on_openai_request(self, request: Request, context: PolicyContext) -> Request:
        """Essentially a wrapper for simple_on_request (extract string, call, re-insert).

        Args:
            request (Request): The original request
            context (PolicyContext): Policy context (includes observability, scratchpad)
        """
        response_str: str = await self.simple_on_request(request.last_message, context)
        request.messages[-1]["content"] = response_str
        return request

    async def on_openai_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Process non-streaming response through simple_on_response_content and simple_on_response_tool_call.

        Args:
            response: Complete ModelResponse from LLM
            context: Policy context
        Returns:
            Response with transformed content and tool calls
        """
        if not response.choices:
            return response

        for choice in response.choices:
            if not isinstance(choice, Choices):
                raise TypeError(
                    f"Expected choice to be Choices, got {type(choice).__name__}. "
                    "This indicates an unexpected response format from the LLM."
                )

            # Transform text content
            if isinstance(choice.message.content, str):
                choice.message.content = await self.simple_on_response_content(choice.message.content, context)

            # Transform tool calls
            if choice.message.tool_calls:
                transformed_tool_calls = []
                for tool_call in choice.message.tool_calls:
                    transformed = await self.simple_on_response_tool_call(tool_call, context)
                    transformed_tool_calls.append(transformed)
                choice.message.tool_calls = transformed_tool_calls

        return response

    # ===== OpenAI streaming hooks =====

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Buffer all chunks, don't emit yet."""
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """Pass the content block to on_response_content and push the result to the client."""
        # Get the completed content block
        if ctx.original_streaming_response_state.just_completed is None:
            ctx.policy_ctx.record_event(
                "policy.simple_policy.content_complete_warning",
                {"summary": "ingress_state.just_completed is None in on_content_complete"},
            )
            return

        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ContentStreamBlock):
            ctx.policy_ctx.record_event(
                "policy.simple_policy.content_complete_warning",
                {"summary": "ingress_state.just_completed is not ContentStreamBlock in on_content_complete"},
            )
            return

        content = block.content
        transformed = await self.simple_on_response_content(content, ctx.policy_ctx)
        await send_text(ctx, transformed)

        # After sending content, send finish_reason chunk
        # This ensures the finish_reason comes after all content, before message_stop
        last_chunk = get_last_ingress_chunk(ctx)
        if last_chunk and last_chunk.choices and last_chunk.choices[0].finish_reason:
            finish_chunk = create_finish_chunk(
                finish_reason=last_chunk.choices[0].finish_reason,
                model=last_chunk.model,
                chunk_id=last_chunk.id,
            )
            await send_chunk(ctx, finish_chunk)

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """Transform tool call and emit."""
        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ToolCallStreamBlock):
            ctx.policy_ctx.record_event(
                "policy.simple_policy.tool_call_complete_warning",
                {"summary": "ingress_state.just_completed is not ToolCallStreamBlock in on_tool_call_complete"},
            )
            return

        tool_call = block.tool_call
        transformed = await self.simple_on_response_tool_call(tool_call, ctx.policy_ctx)
        await send_tool_call(ctx, transformed)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Buffer deltas, don't emit yet.

        SimplePolicy buffers all content deltas and emits the full transformed content
        in on_content_complete instead of forwarding individual deltas.
        """
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """Buffer deltas, don't emit yet.

        SimplePolicy buffers all tool call deltas and emits the full transformed tool call
        in on_tool_call_complete instead of forwarding individual deltas.
        """
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """Handle finish_reason chunk.

        SimplePolicy handles finish_reason differently depending on content type:
        - Content blocks emit finish_reason in on_content_complete
        - Tool calls emit finish_reason in on_stream_complete
        """
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """Stream complete hook - emit final finish_reason chunk."""
        # Get the finish_reason from the original stream
        finish_reason = ctx.original_streaming_response_state.finish_reason
        if not finish_reason:
            return

        # Content blocks already emit their own finish_reason in on_content_complete
        # Only emit here for tool call responses
        blocks = ctx.original_streaming_response_state.blocks
        has_tool_calls = any(isinstance(b, ToolCallStreamBlock) for b in blocks)

        if has_tool_calls:
            last_chunk = get_last_ingress_chunk(ctx)
            chunk_id = last_chunk.id if last_chunk else None
            model = last_chunk.model if last_chunk else "luthien-policy"

            finish_chunk = create_finish_chunk(
                finish_reason=finish_reason,
                model=model,
                chunk_id=chunk_id,
            )
            await send_chunk(ctx, finish_chunk)

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called after all streaming policy processing completes.

        This hook is guaranteed to run even if errors occurred during policy processing.
        SimplePolicy uses this to clear any accumulated buffers.
        """
        pass

    # ===== Anthropic non-streaming hooks =====

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
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
        self, event: AnthropicStreamEvent, context: PolicyContext
    ) -> list[AnthropicStreamEvent]:
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

            return [event]

        # Content block delta: accumulate in buffer, suppress output
        if isinstance(event, RawContentBlockDeltaEvent):
            index = event.index
            delta = event.delta

            if isinstance(delta, TextDelta):
                # Accumulate text delta
                if index not in self._text_buffer:
                    raise RuntimeError(
                        f"Received TextDelta for index {index} but no buffer exists. "
                        "This indicates a missing content_block_start event."
                    )
                self._text_buffer[index] += delta.text
                # Suppress the delta - we'll emit on stop
                return []

            if isinstance(delta, InputJSONDelta):
                # Accumulate JSON delta for tool calls
                if index not in self._tool_buffer:
                    raise RuntimeError(
                        f"Received InputJSONDelta for index {index} but no buffer exists. "
                        "This indicates a missing content_block_start event."
                    )
                self._tool_buffer[index]["input_json"] += delta.partial_json
                # Suppress the delta - we'll emit on stop
                return []

            # Other delta types (thinking, etc.) pass through unchanged
            return [event]

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
                context.record_event(
                    "policy.simple_policy.emit_transformed",
                    {"index": index, "content_length": len(transformed)},
                )
                return [delta_event, event]

            # Handle tool call completion
            if index in self._tool_buffer:
                tool_info = self._tool_buffer.pop(index)

                # Parse the accumulated JSON - empty string means no input
                input_data = json.loads(tool_info["input_json"]) if tool_info["input_json"] else {}

                tool_block: AnthropicToolUseBlock = {
                    "type": "tool_use",
                    "id": tool_info["id"],
                    "name": tool_info["name"],
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
