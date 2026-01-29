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
from typing import TYPE_CHECKING, cast

from litellm.types.utils import Choices, StreamingChoices

from luthien_proxy.policies.base_policy import BasePolicy
from luthien_proxy.policy_core import PolicyContext

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )

logger = logging.getLogger(__name__)


class AllCapsPolicy(BasePolicy):
    """Policy that converts all response content to uppercase.

    This is a simple example policy that demonstrates basic content transformation.
    It operates on both streaming and non-streaming responses. All content is converted
    to ALLCAPS, while tool calls are unmodified.
    """

    async def on_chunk_received(self, ctx):
        """Because AllCapsPolicy capitalizes content but not tool call deltas, we handle logic in specialized hooks; no-op here."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext):
        """Pass through tool call deltas without modification."""
        last_chunk: ModelResponse = ctx.last_chunk_received
        if not last_chunk.choices:
            ctx.policy_ctx.record_event(
                "policy.all_caps.tool_call_delta_warning",
                {
                    "summary": "on_tool_call_delta most recent chunk does not appear to be a tool call delta; dropping chunk"
                },
            )
            return
        # Cast to StreamingChoices for delta access (streaming chunks use StreamingChoices, not Choices)
        streaming_choice = cast(StreamingChoices, last_chunk.choices[0])
        if not hasattr(streaming_choice, "delta"):
            ctx.policy_ctx.record_event(
                "policy.all_caps.tool_call_delta_warning",
                {
                    "summary": "on_tool_call_delta most recent chunk does not appear to be a tool call delta; dropping chunk"
                },
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
            # Cast to StreamingChoices for delta access (streaming chunks use StreamingChoices, not Choices)
            streaming_choice = cast(StreamingChoices, choice)
            # Check for delta presence (streaming chunk indicator)
            if not hasattr(streaming_choice, "delta") or streaming_choice.delta is None:
                ctx.policy_ctx.record_event(
                    "policy.all_caps.content_delta_warning",
                    {"summary": "on_content_delta most recent chunk does not appear to be a content delta"},
                )
                continue

            if streaming_choice.delta.content is None:
                # No content to modify
                continue

            original = streaming_choice.delta.content
            uppercased = original.upper()

            streaming_choice.delta.content = uppercased

            # Emit event for observability
            ctx.policy_ctx.record_event(
                "policy.all_caps.content_transformed",
                {"original_length": len(original), "transformed_length": len(uppercased)},
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
                context.record_event(
                    "policy.all_caps.response_content_warning",
                    {"summary": "Response choice content is not a string, skipping"},
                )
                continue
            orig = choice.message.content
            choice.message.content = choice.message.content.upper()
            context.record_event(
                "policy.all_caps.response_content_transformed",
                {"original_length": len(orig), "transformed_length": len(choice.message.content)},
            )
        return response


__all__ = ["AllCapsPolicy"]
