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
      class: "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
      config: {}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from litellm.types.utils import Choices, StreamingChoices

from luthien_proxy.policy_core import PolicyContext

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse, StreamingChoices

    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )


from luthien_proxy.policies.base_policy import BasePolicy

logger = logging.getLogger(__name__)


class AllCapsPolicy(BasePolicy):
    """Policy that converts all response content to uppercase.

    This is a simple example policy that demonstrates basic content transformation.
    It operates on both streaming and non-streaming responses.
    """

    def __init__(self):
        """Initialize AllCapsPolicy."""
        pass

    async def on_chunk_received(self, ctx):
        """Called on every chunk. Pass through chunks that aren't handled by specialized hooks."""
        # Don't push here - let the specialized hooks (on_content_delta, on_tool_call_delta) handle their chunks
        pass

    async def on_tool_call_delta(self, ctx):
        """Pass through tool call deltas without modification."""
        last_chunk: ModelResponse = ctx.last_chunk_received
        if not last_chunk.choices or not isinstance(last_chunk.choices[0], StreamingChoices):
            ctx.observability.emit_event_nonblocking(
                "policy.all_caps.tool_call_delta_warning",
                {
                    "summary": f"on_tool_call_delta most recent chunk does not appear to be a tool call delta:\n {last_chunk}; dropping chunk"
                },
                level="ERROR",
            )
            return
        ctx.push_chunk(last_chunk)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Convert content delta to uppercase (in-place).

        Args:
            ctx: Streaming policy context with current chunk
        """
        # Get current content delta from most recent chunk
        last_chunk: ModelResponse = ctx.last_chunk_received
        for choice in last_chunk.choices:
            if not isinstance(choice, StreamingChoices):
                ctx.observability.emit_event_nonblocking(
                    "policy.all_caps.content_delta_warning",
                    {
                        "summary": f"on_content_delta most recent chunk does not appear to be a content delta:\n {last_chunk}"
                    },
                )
                continue

            if choice.delta.content is None:
                # No content to modify
                continue

            original = choice.delta.content
            uppercased = original.upper()

            choice.delta.content = uppercased

            # Emit event for observability
            ctx.observability.emit_event_nonblocking(
                "policy.all_caps.content_transformed",
                {
                    "summary": f"Converted content delta {original} to {uppercased}",
                },
            )
        ctx.push_chunk(last_chunk)

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

        for choice in response.choices:
            if not (isinstance(choice, Choices) and isinstance(choice.message.content, str)):
                context.observability.emit_event_nonblocking(
                    "policy.all_caps.response_content_warning",
                    {"summary": f"Response choice content is not a string, skipping:\n {choice}"},
                )
                continue
            orig = choice.message.content
            choice.message.content = choice.message.content.upper()
            context.observability.emit_event_nonblocking(
                "policy.all_caps.response_content_transformed",
                {
                    "summary": f"Converted response {orig} to {choice.message.content}",
                },
            )
        return response


__all__ = ["AllCapsPolicy"]
