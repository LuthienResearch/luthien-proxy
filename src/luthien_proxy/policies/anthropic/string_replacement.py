# ABOUTME: String replacement policy implementing AnthropicPolicyProtocol that replaces strings in text content
"""String replacement policy for Anthropic-native requests.

This policy replaces specified strings in response content with replacement values.
It supports case-insensitive matching with intelligent capitalization preservation.

Example config:
    policy:
      class: "luthien_proxy.policies.anthropic.string_replacement:AnthropicStringReplacementPolicy"
      config:
        replacements:
          - ["foo", "bar"]
          - ["hello", "goodbye"]
        match_capitalization: true
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anthropic.types import (
    RawContentBlockDeltaEvent,
    TextDelta,
)

from luthien_proxy.policies.string_replacement_policy import apply_replacements

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.anthropic_protocol import AnthropicStreamEvent
    from luthien_proxy.policy_core.policy_context import PolicyContext


class AnthropicStringReplacementPolicy:
    """Policy that replaces specified strings in response content.

    This policy supports:
    - Multiple string replacements applied in order
    - Case-insensitive matching with capitalization preservation
    - Both streaming and non-streaming responses

    Implements AnthropicPolicyProtocol:
    - on_request passes through unchanged
    - on_response transforms text content blocks with replacements
    - on_stream_event transforms text_delta deltas with replacements
    """

    def __init__(
        self,
        replacements: list[list[str]] | None = None,
        match_capitalization: bool = False,
    ):
        """Initialize the policy.

        Args:
            replacements: List of [from_string, to_string] pairs.
                Each pair specifies a string to find and its replacement.
            match_capitalization: If True, matches are case-insensitive and
                the replacement preserves the source's capitalization pattern.
        """
        self._replacements: list[tuple[str, str]] = []
        if replacements:
            self._replacements = [(pair[0], pair[1]) for pair in replacements]
        self._match_capitalization = match_capitalization

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "AnthropicStringReplacement"

    def get_config(self) -> dict:
        """Return the configuration for this policy instance."""
        return {
            "replacements": [[f, t] for f, t in self._replacements],
            "match_capitalization": self._match_capitalization,
        }

    def _apply_replacements(self, text: str) -> str:
        """Apply all configured replacements to the given text."""
        return apply_replacements(text, self._replacements, self._match_capitalization)

    async def on_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Pass through request unchanged."""
        return request

    async def on_response(self, response: "AnthropicResponse", context: "PolicyContext") -> "AnthropicResponse":
        """Transform text content blocks with string replacements.

        Iterates through content blocks and applies replacements to text blocks.
        Tool use, thinking, and other block types remain unchanged.
        """
        for block in response.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                text = block.get("text")
                if isinstance(text, str):
                    original = text
                    transformed = self._apply_replacements(text)
                    block["text"] = transformed

                    if original != transformed:
                        context.record_event(
                            "policy.anthropic_string_replacement.content_transformed",
                            {
                                "original_length": len(original),
                                "transformed_length": len(transformed),
                                "replacements_count": len(self._replacements),
                            },
                        )
        return response

    async def on_stream_event(
        self, event: "AnthropicStreamEvent", context: "PolicyContext"
    ) -> "AnthropicStreamEvent | None":
        """Transform text_delta events with string replacements.

        For content_block_delta events with delta.type == "text_delta",
        creates a new event with replaced text instead of mutating the original.
        This avoids potential issues with SDK internal state.
        """
        if not isinstance(event, RawContentBlockDeltaEvent):
            return event

        if isinstance(event.delta, TextDelta):
            original = event.delta.text
            transformed = self._apply_replacements(original)
            new_delta = event.delta.model_copy(update={"text": transformed})
            return event.model_copy(update={"delta": new_delta})

        return event


__all__ = ["AnthropicStringReplacementPolicy"]
