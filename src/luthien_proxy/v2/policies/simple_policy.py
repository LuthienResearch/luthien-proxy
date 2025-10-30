# ABOUTME: SimplePolicy base class for content-level transformations  # noqa: D100
# ABOUTME: Buffers streaming content and applies transformations when blocks complete

"""Module docstring."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from luthien_proxy.v2.policies.policy import Policy

if TYPE_CHECKING:
    from litellm.types.utils import ChatCompletionMessageToolCall, ModelResponse

    from luthien_proxy.v2.messages import Request
    from luthien_proxy.v2.policies.policy import PolicyContext
    from luthien_proxy.v2.streaming.streaming_response_context import (
        StreamingResponseContext,
    )

from litellm.types.utils import StreamingChoices


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
        """Transform content and emit."""
        from luthien_proxy.v2.streaming.helpers import passthrough_accumulated_chunks, send_text
        from luthien_proxy.v2.streaming.stream_blocks import ContentStreamBlock

        # Get the completed content block
        if not ctx.ingress_state.just_completed:
            return

        block = ctx.ingress_state.just_completed
        if not isinstance(block, ContentStreamBlock):
            return

        content = block.content
        transformed = await self.on_response_content(content, ctx.final_request)

        if transformed != content:
            await send_text(ctx, transformed)
        else:
            await passthrough_accumulated_chunks(ctx)

    async def on_tool_call_complete(self, ctx: StreamingResponseContext) -> None:
        """Transform tool call and emit."""
        from luthien_proxy.v2.streaming.helpers import passthrough_accumulated_chunks, send_tool_call
        from luthien_proxy.v2.streaming.stream_blocks import ToolCallStreamBlock

        if not ctx.ingress_state.just_completed:
            return

        block = ctx.ingress_state.just_completed
        if not isinstance(block, ToolCallStreamBlock):
            return

        tool_call = block.tool_call
        transformed = await self.on_response_tool_call(tool_call, ctx.final_request)

        if transformed != tool_call:
            await send_tool_call(ctx, transformed)
        else:
            await passthrough_accumulated_chunks(ctx)

    async def on_content_delta(self, ctx: StreamingResponseContext) -> None:
        """Buffer deltas, don't emit yet."""
        pass  # Assembler buffers

    async def on_tool_call_delta(self, ctx: StreamingResponseContext) -> None:
        """Buffer tool call deltas, don't emit yet."""
        pass  # Assembler buffers

    async def on_chunk_received(self, ctx: StreamingResponseContext) -> None:
        """Pass through metadata chunks immediately."""
        from luthien_proxy.v2.streaming.helpers import get_last_ingress_chunk, send_chunk

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
