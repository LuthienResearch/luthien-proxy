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
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse, StreamingChoices

    from luthien_proxy.v2.streaming.protocol import PolicyContext
    from luthien_proxy.v2.streaming.streaming_policy_context import (
        StreamingPolicyContext,
    )

from litellm.types.utils import Choices

from luthien_proxy.v2.policies.policy import Policy

logger = logging.getLogger(__name__)


class AllCapsPolicy(Policy):
    """Policy that converts all response content to uppercase.

    This is a simple example policy that demonstrates basic content transformation.
    It operates on both streaming and non-streaming responses.
    """

    def __init__(self):
        """Initialize AllCapsPolicy."""
        self._total_chars_converted = 0
        logger.info("AllCapsPolicy initialized")

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
        choice = cast(StreamingChoices, choice)
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

    async def process_full_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
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
            # Cast to Choices (non-streaming) since this is process_full_response
            choice = cast(Choices, choice)
            message = choice.message
            if hasattr(message, "content") and message.content:
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


__all__ = ["AllCapsPolicy"]
