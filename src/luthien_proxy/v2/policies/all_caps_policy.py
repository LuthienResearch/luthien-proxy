# ABOUTME: Simple policy that converts all response content to uppercase
# ABOUTME: Demonstrates basic content transformation using the new Policy interface

"""AllCapsPolicy - Simple content transformation example.

This policy converts all text content in responses to uppercase.
It demonstrates:
- Simple content transformation on both streaming and non-streaming responses
- Modifying content deltas in place
- Event emission for observability

Example config:
    policy:
      class: "luthien_proxy.v2.policies.all_caps_policy:AllCapsPolicy"
      config: {}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse, StreamingChoices

    from luthien_proxy.v2.messages import Request
    from luthien_proxy.v2.policies.policy_context import PolicyContext
    from luthien_proxy.v2.streaming.streaming_policy_context import (
        StreamingPolicyContext,
    )

from luthien_proxy.v2.policies.policy import PolicyProtocol

logger = logging.getLogger(__name__)


class AllCapsPolicy(PolicyProtocol):
    """Policy that converts all response content to uppercase.

    This is a simple example policy that demonstrates basic content transformation.
    It operates on both streaming and non-streaming responses.
    """

    def __init__(self):
        """Initialize AllCapsPolicy."""
        self._total_chars_converted = 0
        logger.info("AllCapsPolicy initialized")

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass through request without modification.

        Args:
            request: The incoming request
            context: Policy context

        Returns:
            Unmodified request
        """
        return request

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Forward modified chunks to egress queue.

        This is called after on_content_delta has modified the chunk in place,
        so we forward the modified chunk to the client.

        Args:
            ctx: Streaming policy context
        """
        # Get the most recent chunk (which has been modified by on_content_delta if applicable)
        if ctx.original_streaming_response_state.raw_chunks:
            chunk = ctx.original_streaming_response_state.raw_chunks[-1]
            ctx.egress_queue.put_nowait(chunk)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Convert content delta to uppercase.

        Args:
            ctx: Streaming policy context with current chunk
        """
        # Get current content delta from most recent chunk
        if not ctx.original_streaming_response_state.raw_chunks:
            return
        current_chunk = ctx.original_streaming_response_state.raw_chunks[-1]
        if not current_chunk.choices:
            return

        choice = current_chunk.choices[0]
        delta = choice.delta

        # Check if there's text content in the delta
        if hasattr(delta, "content") and delta.content:
            # Convert to uppercase
            original = delta.content
            uppercased = original.upper()

            if uppercased != original:
                # Modify the delta in place
                delta.content = uppercased
                chars_converted = len(original)
                self._total_chars_converted += chars_converted

                # Emit event for observability
                await ctx.observability.emit_event(
                    "policy.all_caps.content_transformed",
                    {
                        "summary": f"Converted {chars_converted} characters to uppercase",
                        "chars_converted": chars_converted,
                        "total_chars_converted": self._total_chars_converted,
                    },
                )

                logger.debug(f"Converted content delta to uppercase: {chars_converted} chars")

    async def on_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Convert non-streaming response content to uppercase.

        Args:
            response: Complete ModelResponse from LLM
            context: Policy context

        Returns:
            Response with uppercased content
        """
        # Process each choice
        if not response.choices:
            return response

        total_chars = 0
        modified_count = 0

        for choice in response.choices:
            message = choice.message
            if not message.content:
                continue
            original = message.content
            uppercased = original.upper()

            if uppercased != original:
                message.content = uppercased
                total_chars += len(original)
                modified_count += 1

        if total_chars > 0:
            # Emit event for observability (shows in activity monitor)
            if context.observability:
                await context.observability.emit_event(
                    "policy.all_caps.content_transformed",
                    {
                        "summary": f"Converted {total_chars} characters to uppercase in {modified_count} choice(s)",
                        "chars_converted": total_chars,
                        "choices_modified": modified_count,
                    },
                )

            logger.info(f"Converted non-streaming response content to uppercase: {total_chars} chars")

        return response

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called when content block completes - no action needed."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """Called when tool call delta received - no action needed."""
        pass

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called when tool call block completes - no action needed."""
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """Called when finish_reason received - no action needed."""
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called when stream completes - no action needed."""
        pass


__all__ = ["AllCapsPolicy"]
