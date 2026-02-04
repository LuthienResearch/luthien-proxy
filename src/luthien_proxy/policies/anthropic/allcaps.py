# ABOUTME: AllCaps policy implementing AnthropicPolicyProtocol that transforms text to uppercase
"""AllCaps policy for Anthropic-native requests.

This policy transforms all text content to uppercase while leaving
other content types (tool_use, thinking, etc.) unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anthropic.types import (
    RawContentBlockDeltaEvent,
    TextDelta,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.anthropic_protocol import AnthropicStreamEvent
    from luthien_proxy.policy_core.policy_context import PolicyContext


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
            # Narrow type to text block by checking type field
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                text = block.get("text")
                if isinstance(text, str):
                    block["text"] = text.upper()
        return response

    async def on_stream_event(
        self, event: "AnthropicStreamEvent", context: "PolicyContext"
    ) -> "AnthropicStreamEvent | None":
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


__all__ = ["AnthropicAllCapsPolicy"]
