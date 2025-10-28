# ABOUTME: Simplified event-based policy that works with complete blocks instead of streaming deltas
# ABOUTME: Suppresses delta forwarding and provides hooks that receive complete content/tool calls

"""Simplified event-based policy for working with complete blocks.

This module provides SimpleEventBasedPolicy, which hides streaming complexity
and provides a beginner-friendly interface. Policies override:

1. on_request(request) -> request  (optional, default: pass through)
2. on_content_simple(content_text) -> modified_content_text
3. on_tool_call_simple(tool_call_block) -> modified_tool_call_block | str | None

The base class suppresses delta forwarding, so your hooks receive complete
blocks that have already been buffered by StreamingChunkAssembler. You just transform
and return the result.

Example:
    class UppercasePolicy(SimpleEventBasedPolicy):
        async def on_content_simple(
            self,
            content: str,
            context: PolicyContext,
            streaming_ctx: StreamingContext,
        ) -> str:
            return content.upper()

        # Tool calls pass through unchanged (default implementation)
"""

from __future__ import annotations

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


class SimpleEventBasedPolicy(EventBasedPolicy):
    """Simplified policy that works with complete blocks instead of streaming deltas.

    Suppresses delta forwarding and calls simplified hooks with complete data.
    StreamingChunkAssembler already buffers deltas into blocks - this just waits for
    completion signals and calls your hooks.

    Subclasses implement:
    - on_request: Process full request before sending to LLM (optional)
    - on_content_simple: Process complete text content (optional)
    - on_tool_call_simple: Process complete tool call (optional)

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

    async def on_content_simple(
        self,
        content: str,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> str:
        """Process complete content text from response.

        This is called once the content block is complete (StreamingChunkAssembler
        already buffered all the deltas). Transform the content and return
        the modified version.

        Note: Content may be followed by tool calls in the same response.

        Default: return unchanged.

        Args:
            content: Complete text content (already buffered)
            context: Per-request context
            streaming_ctx: Streaming context for metadata

        Returns:
            Modified content text to send to client
        """
        return content

    async def on_tool_call_simple(
        self,
        tool_call: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> ToolCallStreamBlock | str | None:
        """Process complete tool call from response.

        This is called once the tool call block is complete (StreamingChunkAssembler
        already buffered all the deltas). You can:
        - Return modified tool call block to send to client
        - Return a string to replace the tool call with text content
        - Return None to block/filter this tool call
        - Raise exception to reject the entire response

        Default: return unchanged (pass through).

        Args:
            tool_call: Complete tool call block (name and arguments available)
            context: Per-request context
            streaming_ctx: Streaming context for metadata

        Returns:
            Modified tool call block, string content to replace tool call, or None to block
        """
        return tool_call

    # ------------------------------------------------------------------
    # EventBasedPolicy overrides - suppress deltas, handle completion
    # ------------------------------------------------------------------

    async def on_content_delta(
        self,
        delta: str,
        block: ContentStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Suppress content delta forwarding.

        Content will be sent in on_content_complete() after processing.
        """
        pass

    async def on_content_complete(
        self,
        block: ContentStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Process complete content block and send result.

        Block is already complete (buffered by StreamingChunkAssembler).
        Call simplified hook and send result.
        """
        # Call simplified hook with complete content
        modified_content = await self.on_content_simple(
            block.content,
            context,
            streaming_ctx,
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
        """Suppress tool call delta forwarding.

        Tool call will be sent in on_tool_call_complete() after processing.
        """
        pass

    async def on_tool_call_complete(
        self,
        block: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Process complete tool call block and send result.

        Block is already complete (buffered by StreamingChunkAssembler).
        Call simplified hook and handle result.
        """
        # Call simplified hook with complete tool call
        result = await self.on_tool_call_simple(
            block,
            context,
            streaming_ctx,
        )

        # Handle different return types
        if result is None:
            # Tool call was blocked - don't send anything
            context.emit(
                event_type="simple_policy.tool_call_blocked",
                summary=f"Tool call blocked: {block.name}",
                details={
                    "tool_name": block.name,
                    "tool_id": block.id,
                },
                severity="warning",
            )
        elif isinstance(result, str):
            # Tool call was replaced with text content
            context.emit(
                event_type="simple_policy.tool_call_replaced",
                summary=f"Tool call replaced with text: {block.name}",
                details={
                    "tool_name": block.name,
                    "content_length": len(result),
                },
            )
            await streaming_ctx.send_text(result)
        else:
            # Tool call was returned (possibly modified)
            chunk = build_block_chunk(result, model=context.request.model)
            await streaming_ctx.send(chunk)

    # ------------------------------------------------------------------
    # Non-streaming support
    # ------------------------------------------------------------------

    async def on_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process non-streaming response.

        Extract content and tool calls, pass through simplified hooks,
        and reconstruct the response.
        """
        if not response.choices or len(response.choices) == 0:
            return response

        choice = response.choices[0]
        if not isinstance(choice, Choices):
            return response

        message = choice.message

        # Extract content
        content = message.content if hasattr(message, "content") and message.content else ""

        # Extract tool calls
        tool_calls = message.tool_calls if hasattr(message, "tool_calls") and message.tool_calls else None

        # Create fake streaming context (non-streaming doesn't use it)
        fake_streaming_ctx = None  # type: ignore

        # Process content through hook
        if content:
            modified_content = await self.on_content_simple(
                content,
                context,
                fake_streaming_ctx,  # type: ignore
            )
            if hasattr(message, "content"):
                message.content = modified_content

        # Process tool calls through hooks
        if tool_calls:
            modified_tool_calls = []
            for tc in tool_calls:
                # Extract function data
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
                result = await self.on_tool_call_simple(
                    tool_block,
                    context,
                    fake_streaming_ctx,  # type: ignore
                )

                # Handle return types
                if result is None:
                    # Blocked
                    pass
                elif isinstance(result, str):
                    # Replaced with content
                    if hasattr(message, "content"):
                        if message.content:
                            message.content = message.content + "\n\n" + result
                        else:
                            message.content = result
                else:
                    # Modified tool call
                    modified_tc = ChatCompletionMessageToolCall(
                        id=result.id,
                        type="function",
                        function=Function(
                            name=result.name,
                            arguments=result.arguments,
                        ),
                    )
                    modified_tool_calls.append(modified_tc)

            # Update tool calls
            if hasattr(message, "tool_calls"):
                message.tool_calls = modified_tool_calls if modified_tool_calls else None

        return response


__all__ = [
    "SimpleEventBasedPolicy",
]
