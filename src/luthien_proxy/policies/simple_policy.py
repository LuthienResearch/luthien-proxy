# ABOUTME: SimplePolicy base class. SimplePolicy sacrifices streaming support for simpler policy authoring.
# ABOUTME: Buffers streaming content and applies transformations when blocks complete

"""SimplePolicy base class. SimplePolicy sacrifices streaming support for simpler policy authoring."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.policy_core.streaming_utils import passthrough_accumulated_chunks, send_text, send_tool_call
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock

if TYPE_CHECKING:
    from litellm.types.utils import ChatCompletionMessageToolCall

    from luthien_proxy.messages import Request
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )

logger = logging.getLogger(__name__)


class SimplePolicy(PolicyProtocol):
    """Convenience base class for content-level transformations.

    Buffers streaming content and applies transformations when complete.
    """

    # ===== Simple methods that subclasses override =====

    async def on_request_simple(self, request: Request) -> Request:
        """Transform/validate request before LLM."""
        return request

    async def on_response_content(self, content: str, request: Request) -> str:
        """Transform complete response content."""
        return content

    async def on_response_tool_call(
        self, tool_call: ChatCompletionMessageToolCall, request: Request
    ) -> ChatCompletionMessageToolCall:
        """Transform/validate a complete tool call."""
        return tool_call

    # ===== Implementation of streaming hooks =====

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Delegate to simple method."""
        return await self.on_request_simple(request)

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """Transform content and emit."""
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
        transformed = await self.on_response_content(content, ctx.policy_ctx.request)

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
        transformed = await self.on_response_tool_call(tool_call, ctx.policy_ctx.request)

        if transformed != tool_call:
            await send_tool_call(ctx, transformed)
        else:
            await passthrough_accumulated_chunks(ctx)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Buffer deltas, don't emit yet."""
        pass  # Assembler buffers

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """Buffer tool call deltas, don't emit yet."""
        pass  # Assembler buffers

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Buffer all chunks, don't emit yet."""
        pass  # Assembler buffers


__all__ = ["SimplePolicy"]
