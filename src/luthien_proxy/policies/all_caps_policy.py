"""AllCapsPolicy - Simple content transformation example.

Converts all text content in responses to uppercase using TextModifierPolicy.

Example config:
    policy:
      class: "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
      config: {}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import RawContentBlockDeltaEvent, TextDelta

from luthien_proxy.policy_core import TextModifierPolicy

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext


class AllCapsPolicy(TextModifierPolicy):
    """Policy that converts all response content to uppercase.

    Tool calls, thinking blocks, and images pass through unchanged.
    """

    def modify_text(self, text: str) -> str:
        """Convert text to uppercase."""
        return text.upper()

    # -- Anthropic helpers for MultiParallelPolicy compatibility ---------------
    # MultiParallelPolicy calls on_anthropic_response/on_anthropic_stream_event
    # directly on sub-policies. TextModifierPolicy handles Anthropic via
    # run_anthropic, so we expose these for backward compatibility.

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Transform text blocks to uppercase in Anthropic response."""
        self._modify_anthropic_response(response)
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Transform text_delta events to uppercase in Anthropic streaming."""
        if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
            new_delta = event.delta.model_copy(update={"text": self.modify_text(event.delta.text)})
            return [event.model_copy(update={"delta": new_delta})]
        return [event]


__all__ = ["AllCapsPolicy"]
