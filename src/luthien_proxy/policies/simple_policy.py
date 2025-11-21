# ABOUTME: SimplePolicy base class. SimplePolicy sacrifices streaming support for simpler policy authoring.
# ABOUTME: Buffers streaming content and applies transformations when blocks complete

"""SimplePolicy base class. SimplePolicy sacrifices streaming support for simpler policy authoring."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from luthien_proxy.policies.base_policy import BasePolicy
from luthien_proxy.policy_core.streaming_utils import get_last_ingress_chunk, send_chunk, send_text, send_tool_call
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock

if TYPE_CHECKING:
    from litellm.types.utils import ChatCompletionMessageToolCall

    from luthien_proxy.messages import Request
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )

logger = logging.getLogger(__name__)


class SimplePolicy(BasePolicy):
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

    # ===== Implementation of streaming hooks =====

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Essentially a wrapper for simple_on_request (extract string, call, re-insert).

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
            ctx.observability.emit_event_nonblocking(
                "policy.simple_policy.content_complete_warning",
                {
                    "summary": "ingress_state.just_completed is None in on_content_complete",
                },
                level="ERROR",
            )
            return

        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ContentStreamBlock):
            ctx.observability.emit_event_nonblocking(
                "policy.simple_policy.content_complete_warning",
                {
                    "summary": "ingress_state.just_completed is not ContentStreamBlock in on_content_complete",
                },
                level="ERROR",
            )
            return

        content = block.content
        transformed = await self.simple_on_response_content(content, ctx.policy_ctx)
        await send_text(ctx, transformed)

        # After sending content, send finish_reason chunk
        # This ensures the finish_reason comes after all content, before message_stop
        # TODO: We should be crafting our own finish_reason chunk here based on content outcome
        last_chunk = get_last_ingress_chunk(ctx)
        if last_chunk and last_chunk.choices and last_chunk.choices[0].finish_reason:
            from litellm.types.utils import Delta, ModelResponse, StreamingChoices

            finish_chunk = ModelResponse(
                id=last_chunk.id,
                model=last_chunk.model,
                choices=[
                    StreamingChoices(
                        finish_reason=last_chunk.choices[0].finish_reason,
                        index=0,
                        delta=Delta(content=None, role=None),
                    )
                ],
            )
            await send_chunk(ctx, finish_chunk)

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """Transform tool call and emit."""
        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ToolCallStreamBlock):
            ctx.observability.emit_event_nonblocking(
                "policy.simple_policy.tool_call_complete_warning",
                {
                    "summary": "ingress_state.just_completed is not ToolCallStreamBlock in on_tool_call_complete",
                },
                level="ERROR",
            )
            return

        tool_call = block.tool_call
        transformed = await self.simple_on_response_tool_call(tool_call, ctx.policy_ctx)
        await send_tool_call(ctx, transformed)
        # Note: send_tool_call already includes finish_reason="tool_calls" in the chunk,
        # so we don't need to send a separate finish_reason chunk here

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

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """Stream complete hook."""
        # No-op: finish_reason is already sent in on_content_complete
        pass


__all__ = ["SimplePolicy"]
