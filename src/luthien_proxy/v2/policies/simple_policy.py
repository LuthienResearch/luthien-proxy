# ABOUTME: SimplePolicy base class for content-level transformations  # noqa: D100
# ABOUTME: Buffers streaming content and applies transformations when blocks complete

"""Module docstring."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from litellm.types.utils import StreamingChoices

from luthien_proxy.v2.policies.policy import Policy
from luthien_proxy.v2.streaming.helpers import (
    get_last_ingress_chunk,
    passthrough_last_chunk,
    send_chunk,
    send_text,
    send_tool_call,
)
from luthien_proxy.v2.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock

if TYPE_CHECKING:
    from litellm.types.utils import ChatCompletionMessageToolCall, ModelResponse

    from luthien_proxy.v2.messages import Request
    from luthien_proxy.v2.policies.policy import PolicyContext
    from luthien_proxy.v2.streaming.streaming_response_context import (
        StreamingResponseContext,
    )


class SimplePolicy(Policy):
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

    async def on_content_complete(self, ctx: StreamingResponseContext) -> None:
        """Transform content and emit (only if transformation needed)."""
        # Get the completed content block
        if not ctx.ingress_state.just_completed:
            return

        block = ctx.ingress_state.just_completed
        if not isinstance(block, ContentStreamBlock):
            return

        content = block.content
        transformed = await self.on_response_content(content, ctx.final_request)

        # Only send if transformed (chunks already passed through in on_content_delta)
        if transformed != content:
            await send_text(ctx, transformed)

    async def on_tool_call_complete(self, ctx: StreamingResponseContext) -> None:
        """Transform tool call and emit (only if transformation needed)."""
        if not ctx.ingress_state.just_completed:
            return

        block = ctx.ingress_state.just_completed
        if not isinstance(block, ToolCallStreamBlock):
            return

        tool_call = block.tool_call
        transformed = await self.on_response_tool_call(tool_call, ctx.final_request)

        # Only send if transformed (chunks already passed through in on_tool_call_delta)
        if transformed != tool_call:
            await send_tool_call(ctx, transformed)

    async def on_content_delta(self, ctx: StreamingResponseContext) -> None:
        """Stream content deltas through immediately for true concurrent streaming."""
        # Pass through the chunk immediately to enable concurrent streaming
        await passthrough_last_chunk(ctx)

    async def on_tool_call_delta(self, ctx: StreamingResponseContext) -> None:
        """Stream tool call deltas through immediately for true concurrent streaming."""
        # Pass through the chunk immediately to enable concurrent streaming
        await passthrough_last_chunk(ctx)

    async def on_chunk_received(self, ctx: StreamingResponseContext) -> None:
        """Pass through metadata chunks immediately."""
        chunk = get_last_ingress_chunk(ctx)
        if chunk and not self._has_content_or_tool_delta(chunk):
            await send_chunk(ctx, chunk)

    def _has_content_or_tool_delta(self, chunk: "ModelResponse") -> bool:  # noqa: F821
        """Check if chunk contains content or tool call delta."""
        if not chunk.choices:
            return False
        # Cast to StreamingChoices since this is checking streaming chunks
        streaming_choice = cast(StreamingChoices, chunk.choices[0])
        delta = streaming_choice.delta
        if not delta:
            return False
        return bool(delta.get("content") or delta.get("tool_calls"))


__all__ = ["SimplePolicy"]
