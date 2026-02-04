"""AllCapsPolicy - Simple content transformation example.

This policy converts all text content in responses to uppercase.
It demonstrates:
- Simple content transformation on both streaming and non-streaming responses
- Modifying content deltas in place
- Event emission for observability
- Support for both OpenAI and Anthropic API formats

Example config:
    policy:
      class: "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
      config: {}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from anthropic.types import (
    RawContentBlockDeltaEvent,
    TextDelta,
)
from litellm.types.utils import Choices, StreamingChoices

from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.base_policy import BasePolicy

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )

logger = logging.getLogger(__name__)


class AllCapsPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicPolicyInterface):
    """Policy that converts all response content to uppercase.

    This is a simple example policy that demonstrates basic content transformation.
    It operates on both streaming and non-streaming responses for both OpenAI and
    Anthropic formats. All text content is converted to ALLCAPS, while tool calls
    and other block types are unmodified.
    """

    # =========================================================================
    # OpenAI Interface Implementation
    # =========================================================================

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Pass through request without modification."""
        return request

    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Convert non-streaming response content to uppercase.

        Args:
            response: Complete ModelResponse from LLM
            context: Policy context
        Returns:
            Response with uppercased content
        """
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

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """AllCapsPolicy handles chunks in specialized hooks; no-op here."""
        pass

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Convert content delta to uppercase (in-place).

        Args:
            ctx: Streaming policy context with current chunk
        """
        last_chunk: ModelResponse = ctx.last_chunk_received
        for choice in last_chunk.choices:
            streaming_choice = cast(StreamingChoices, choice)
            if not hasattr(streaming_choice, "delta") or streaming_choice.delta is None:
                ctx.policy_ctx.record_event(
                    "policy.all_caps.content_delta_warning",
                    {"summary": "on_content_delta most recent chunk does not appear to be a content delta"},
                )
                continue

            if streaming_choice.delta.content is None:
                continue

            original = streaming_choice.delta.content
            uppercased = original.upper()

            streaming_choice.delta.content = uppercased

            ctx.policy_ctx.record_event(
                "policy.all_caps.content_transformed",
                {"original_length": len(original), "transformed_length": len(uppercased)},
            )
        ctx.push_chunk(last_chunk)

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """No-op: content transformation happens in on_content_delta."""
        pass

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
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

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """No-op: tool calls are passed through unchanged."""
        pass

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """No-op: finish reason chunks handled elsewhere."""
        pass

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """No-op: no cleanup needed after stream completes."""
        pass

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """No-op: no per-request state to clean up."""
        pass

    # =========================================================================
    # Anthropic Interface Implementation
    # =========================================================================

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Pass through request unchanged."""
        return request

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Transform text content blocks to uppercase.

        Iterates through content blocks and converts text blocks to uppercase.
        Tool use, thinking, and other block types remain unchanged.
        """
        for block in response.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                text = block.get("text")
                if isinstance(text, str):
                    block["text"] = text.upper()
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: "PolicyContext"
    ) -> AnthropicStreamEvent | None:
        """Transform text_delta events to uppercase.

        For content_block_delta events with delta.type == "text_delta",
        creates a new event with uppercase text instead of mutating the original.
        This avoids potential issues with SDK internal state.
        """
        if not isinstance(event, RawContentBlockDeltaEvent):
            return event

        if isinstance(event.delta, TextDelta):
            new_delta = event.delta.model_copy(update={"text": event.delta.text.upper()})
            return event.model_copy(update={"delta": new_delta})

        return event


__all__ = ["AllCapsPolicy"]
