# ABOUTME: Simplified event-based policy that buffers streaming into complete blocks
# ABOUTME: Provides beginner-friendly interface with just three hooks: request, content, tool_call

"""Simplified event-based policy for working with complete blocks.

This module provides SimpleEventBasedPolicy, which hides streaming complexity
and provides a beginner-friendly interface. Policies only need to implement:

1. on_request(request) -> request
2. on_response_content(content_text) -> modified_content_text
3. on_response_tool_call(tool_call_block) -> modified_tool_call_block | None

Behind the scenes, this policy buffers all streaming chunks and only calls
your hooks once complete blocks are available. This makes it easy to write
policies that need to see full content or complete tool calls before deciding
what to do.

Example:
    class UppercasePolicy(SimpleEventBasedPolicy):
        async def on_response_content(
            self,
            content: str,
            context: PolicyContext,
            streaming_ctx: StreamingContext,
        ) -> str:
            return content.upper()

        # Tool calls pass through unchanged (default implementation)
"""

from __future__ import annotations

import logging

from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Function,
    ModelResponse,
)

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.event_based_policy import (
    EventBasedPolicy,
    StreamingContext,
)
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.streaming.stream_blocks import (
    ContentStreamBlock,
    ToolCallStreamBlock,
)
from luthien_proxy.v2.streaming.utils import build_block_chunk

logger = logging.getLogger(__name__)


class SimpleEventBasedPolicy(EventBasedPolicy):
    """Simplified policy that works with complete blocks instead of streaming deltas.

    Buffers streaming responses and calls simplified hooks with complete data.
    Subclasses implement three simple methods:

    - on_request: Process full request before sending to LLM
    - on_response_content: Process complete text content (may be followed by tool calls)
    - on_response_tool_call: Process complete tool call (can modify or block)

    Default implementations pass data through unchanged.
    """

    # ------------------------------------------------------------------
    # Simplified interface - override these in your subclass
    # ------------------------------------------------------------------

    async def on_request(
        self,
        request: Request,
        context: PolicyContext,
    ) -> Request:
        """Process request before sending to LLM.

        Default: return unchanged.

        Args:
            request: Complete request
            context: Per-request context

        Returns:
            Modified request (or raise to reject)
        """
        return request

    async def on_response_content(
        self,
        content: str,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> str:
        """Process complete content text from response.

        This is called once all content chunks have been received and buffered.
        You can transform the content and return the modified version.

        Note: Content may be followed by tool calls in the same response.

        Default: return unchanged.

        Args:
            content: Complete text content
            context: Per-request context
            streaming_ctx: Streaming context for sending output

        Returns:
            Modified content text to send to client
        """
        return content

    async def on_response_tool_call(
        self,
        tool_call: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> ToolCallStreamBlock | str | None:
        """Process complete tool call from response.

        This is called once a complete tool call has been received and buffered.
        You can:
        - Return modified tool call block to send to client
        - Return a string to replace the tool call with text content
        - Return None to block/filter this tool call
        - Raise exception to reject the entire response

        Default: return unchanged (pass through).

        Args:
            tool_call: Complete tool call block (name and arguments available)
            context: Per-request context
            streaming_ctx: Streaming context for sending output

        Returns:
            Modified tool call block, string content to replace tool call, or None to block
        """
        return tool_call

    # ------------------------------------------------------------------
    # EventBasedPolicy overrides - handle both streaming and non-streaming
    # ------------------------------------------------------------------

    async def on_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process non-streaming response by extracting content and tool calls.

        For non-streaming responses, we extract the content and tool calls,
        pass them through the simplified hooks, and reconstruct the response.
        """
        if not response.choices or len(response.choices) == 0:
            return response

        choice = response.choices[0]
        # Non-streaming responses use Choices with message attribute
        if not isinstance(choice, Choices):
            # This is a streaming choice, not a non-streaming one - shouldn't happen
            return response

        message = choice.message

        # Extract content
        content = message.content if hasattr(message, "content") and message.content else ""

        # Extract tool calls
        tool_calls = message.tool_calls if hasattr(message, "tool_calls") and message.tool_calls else None

        # Emit event for observability
        context.emit(
            event_type="simple_policy.non_streaming_processing",
            summary="Processing non-streaming response",
            details={
                "has_content": bool(content),
                "has_tool_calls": bool(tool_calls),
                "num_tool_calls": len(tool_calls) if tool_calls else 0,
                "policy_class": self.__class__.__name__,
            },
        )

        # Create a fake streaming context (non-streaming doesn't use it)
        # We'll modify the response in place rather than sending chunks
        fake_streaming_ctx = None  # type: ignore

        # Process content through hook
        if content:
            modified_content = await self.on_response_content(
                content,
                context,
                fake_streaming_ctx,  # type: ignore
            )
            # Update the message content
            if hasattr(message, "content"):
                message.content = modified_content

        # Process tool calls through hooks
        if tool_calls:
            modified_tool_calls = []
            for tc in tool_calls:
                # Extract function name and arguments safely
                func_name = ""
                func_args = ""
                if tc.function:
                    func_name = tc.function.name if tc.function.name else ""
                    func_args = tc.function.arguments if tc.function.arguments else ""

                # Convert to ToolCallStreamBlock
                tool_block = ToolCallStreamBlock(
                    id=tc.id,
                    index=len(modified_tool_calls),
                    name=func_name,
                    arguments=func_args,
                )
                tool_block.is_complete = True

                # Pass through hook
                result = await self.on_response_tool_call(
                    tool_block,
                    context,
                    fake_streaming_ctx,  # type: ignore
                )

                # Handle different return types
                if result is None:
                    # Tool call was blocked - don't add to list
                    pass
                elif isinstance(result, str):
                    # Tool call was replaced with content - append to message content
                    if hasattr(message, "content"):
                        if message.content:
                            message.content = message.content + "\n\n" + result
                        else:
                            message.content = result
                else:
                    # Tool call was returned (possibly modified)
                    # Convert back to ChatCompletionMessageToolCall
                    modified_tc = ChatCompletionMessageToolCall(
                        id=result.id,
                        type="function",
                        function=Function(
                            name=result.name,
                            arguments=result.arguments,
                        ),
                    )
                    modified_tool_calls.append(modified_tc)

            # Update tool calls in message
            if hasattr(message, "tool_calls"):
                message.tool_calls = modified_tool_calls if modified_tool_calls else None

        return response

    async def on_content_delta(
        self,
        delta: str,
        block: ContentStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Buffer content deltas - don't forward until complete.

        Override default behavior to prevent immediate forwarding.
        Content will be sent in on_content_complete() after processing.
        """
        # Just buffer - don't send yet
        pass

    async def on_content_complete(
        self,
        block: ContentStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Process complete content through on_response_content() and send result."""
        # Emit event for observability
        context.emit(
            event_type="simple_policy.content_processing",
            summary="Processing complete content block",
            details={
                "content_length": len(block.content),
                "policy_class": self.__class__.__name__,
            },
        )

        # Call simplified hook with complete content
        modified_content = await self.on_response_content(
            block.content,
            context,
            streaming_ctx,
        )

        # Emit event if content was modified
        if modified_content != block.content:
            context.emit(
                event_type="simple_policy.content_modified",
                summary="Content was modified by policy",
                details={
                    "original_length": len(block.content),
                    "modified_length": len(modified_content),
                    "policy_class": self.__class__.__name__,
                },
            )

        # Send the (possibly modified) content as a single chunk
        if modified_content:
            await streaming_ctx.send_text(modified_content)

    async def on_tool_call_delta(
        self,
        raw_chunk: ModelResponse,
        block: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Buffer tool call deltas - don't forward until complete.

        Override default behavior to prevent immediate forwarding.
        Tool call will be sent in on_tool_call_complete() after processing.
        """
        # Just buffer - don't send yet
        pass

    async def on_tool_call_complete(
        self,
        block: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Process complete tool call through on_response_tool_call() and send result."""
        # Emit event for observability
        context.emit(
            event_type="simple_policy.tool_call_processing",
            summary=f"Processing complete tool call: {block.name}",
            details={
                "tool_name": block.name,
                "tool_id": block.id,
                "args_length": len(block.arguments),
                "policy_class": self.__class__.__name__,
            },
        )

        # Call simplified hook with complete tool call
        result = await self.on_response_tool_call(
            block,
            context,
            streaming_ctx,
        )

        # Handle different return types
        if result is None:
            # Tool call was blocked
            context.emit(
                event_type="simple_policy.tool_call_blocked",
                summary=f"Tool call blocked by policy: {block.name}",
                details={
                    "tool_name": block.name,
                    "tool_id": block.id,
                    "policy_class": self.__class__.__name__,
                },
                severity="warning",
            )
        elif isinstance(result, str):
            # Tool call was replaced with content
            context.emit(
                event_type="simple_policy.tool_call_replaced_with_content",
                summary=f"Tool call replaced with content: {block.name}",
                details={
                    "tool_name": block.name,
                    "tool_id": block.id,
                    "content_length": len(result),
                    "policy_class": self.__class__.__name__,
                },
                severity="info",
            )
            # Send as text content instead of tool call
            await streaming_ctx.send_text(result)
        else:
            # Tool call was returned (possibly modified)
            if result.name != block.name or result.arguments != block.arguments:
                context.emit(
                    event_type="simple_policy.tool_call_modified",
                    summary=f"Tool call modified by policy: {block.name}",
                    details={
                        "original_name": block.name,
                        "modified_name": result.name,
                        "name_changed": result.name != block.name,
                        "args_changed": result.arguments != block.arguments,
                        "policy_class": self.__class__.__name__,
                    },
                )
            # Send the tool call
            chunk = build_block_chunk(result, model=context.request.model)
            await streaming_ctx.send(chunk)


__all__ = [
    "SimpleEventBasedPolicy",
]
