# ABOUTME: SimplePolicy base class. SimplePolicy sacrifices streaming support for simpler policy authoring.
# ABOUTME: Buffers streaming content and applies transformations when blocks complete

"""SimplePolicy base class. SimplePolicy sacrifices streaming support for simpler policy authoring."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from luthien_proxy.policy_core.streaming_utils import passthrough_accumulated_chunks, send_text, send_tool_call
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock

if TYPE_CHECKING:
    from litellm.types.utils import ChatCompletionMessageToolCall

    from luthien_proxy.messages import Request
    from luthien_proxy.policies.base_policy import BasePolicy
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )

logger = logging.getLogger(__name__)


class SimplePolicy(BasePolicy):
    """Convenience base class for content-level transformations.

    Buffers streaming content and applies transformations when complete.
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy. Defaults to class name."""
        return self.__class__.__name__

    # ===== Simple methods that subclasses override =====

    async def simple_on_request(self, request_str: str, context: PolicyContext) -> str:
        """Transform request string. Override to implement request transformations.

        Args:
            request_str (str): The original request as a string
            context (PolicyContext): Policy context (includes observability, scratchpad)
        """
        return request_str

    async def on_response_content(self, content: str, context: PolicyContext) -> str:
        """Transform complete response content. Override to implement content transformations.

        Args:
            content (str): Complete response content ("Hello user")
            context (PolicyContext): Policy context (includes request, response metadata, observability, scratchpad)

        """
        return content

    async def on_response_tool_call(
        self, tool_call: ChatCompletionMessageToolCall, context: PolicyContext
    ) -> ChatCompletionMessageToolCall:
        """Transform/validate a complete tool call. Override to implement tool call transformations.

        Args:
            tool_call (ChatCompletionMessageToolCall): The complete tool call
            context (PolicyContext): Policy context (includes request, response metadata, observability, scratchpad)
        """
        return tool_call

    # ===== Implementation of streaming hooks =====

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass through request without modification. Override to implement request transformations.

        Args:
            request (Request): The original request
            context (PolicyContext): Policy context (includes observability, scratchpad)
        """
        response_str: str = await self.simple_on_request(request.last_message, context)
        request.messages[-1].content = response_str
        return request

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Buffer all chunks, don't emit yet."""
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """Pass the content block to on_response_content and push the result to the client."""
        # Get the completed content block
        if ctx.original_streaming_response_state.just_completed is None:
            logger.error("ingress_state.just_completed is None in on_content_complete")
            return

        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ContentStreamBlock):
            logger.error("ingress_state.just_completed is not ContentStreamBlock in on_content_complete")
            return

        content = block.content
        # Request is always set by gateway before streaming begins
        assert ctx.policy_ctx.request is not None, "Request must be set in policy context"
        transformed = await self.on_response_content(content, ctx.policy_ctx)

        if transformed != content:
            await send_text(ctx, transformed)
        else:
            await passthrough_accumulated_chunks(ctx)

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """Transform tool call and emit.

        Raises:
            RuntimeError: If just_completed is None (indicates orchestrator bug)
        """
        if ctx.original_streaming_response_state.just_completed is None:
            raise RuntimeError("on_tool_call_complete called but just_completed is None - this should not happen")

        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ToolCallStreamBlock):
            return

        tool_call = block.tool_call
        # Request is always set by gateway before streaming begins
        assert ctx.policy_ctx.request is not None, "Request must be set in policy context"
        transformed = await self.on_response_tool_call(tool_call, ctx.policy_ctx)

        if transformed != tool_call:
            await send_tool_call(ctx, transformed)
        else:
            await passthrough_accumulated_chunks(ctx)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Buffer deltas, don't emit yet."""
        pass


__all__ = ["SimplePolicy"]
