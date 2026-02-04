# ABOUTME: AllCaps policy implementing AnthropicPolicyProtocol that transforms text to uppercase
"""AllCaps policy for Anthropic-native requests.

This policy transforms all text content to uppercase while leaving
other content types (tool_use, thinking, etc.) unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from luthien_proxy.llm.types.anthropic import (
    AnthropicContentBlockDeltaEvent,
    AnthropicTextBlock,
    AnthropicTextDelta,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
        AnthropicStreamingEvent,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext


def _is_text_block(block: Any) -> bool:
    """Check if a content block is a text block."""
    return isinstance(block, dict) and block.get("type") == "text"


def _is_text_delta(delta: Any) -> bool:
    """Check if a delta is a text delta."""
    return isinstance(delta, dict) and delta.get("type") == "text_delta"


class AnthropicAllCapsPolicy:
    """Policy that transforms all text content to uppercase.

    Implements AnthropicPolicyProtocol:
    - on_request passes through unchanged
    - on_response transforms text content blocks to uppercase
    - on_stream_event transforms text_delta deltas to uppercase
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "AnthropicAllCaps"

    async def on_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Pass through request unchanged."""
        return request

    async def on_response(self, response: "AnthropicResponse", context: "PolicyContext") -> "AnthropicResponse":
        """Transform text content blocks to uppercase.

        Iterates through content blocks and converts text blocks to uppercase.
        Tool use, thinking, and other block types remain unchanged.
        """
        for block in response.get("content", []):
            if _is_text_block(block):
                text_block = cast(AnthropicTextBlock, block)
                text_block["text"] = text_block["text"].upper()
        return response

    async def on_stream_event(
        self, event: "AnthropicStreamingEvent", context: "PolicyContext"
    ) -> "AnthropicStreamingEvent | None":
        """Transform text_delta events to uppercase.

        For content_block_delta events with delta.type == "text_delta",
        converts the text to uppercase. All other events pass through unchanged.
        """
        if not isinstance(event, dict) or event.get("type") != "content_block_delta":
            return event

        delta_event = cast(AnthropicContentBlockDeltaEvent, event)
        delta = delta_event.get("delta")
        if delta is not None and _is_text_delta(delta):
            text_delta = cast(AnthropicTextDelta, delta)
            text_delta["text"] = text_delta["text"].upper()

        return event


__all__ = ["AnthropicAllCapsPolicy"]
