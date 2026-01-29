"""StringReplacementPolicy - Replace strings in LLM responses.

This policy replaces specified strings in response content with replacement values.
It supports case-insensitive matching with intelligent capitalization preservation.

Example config:
    policy:
      class: "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
      config:
        replacements:
          - ["foo", "bar"]
          - ["hello", "goodbye"]
        match_capitalization: true
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from litellm.types.utils import Choices, StreamingChoices

from luthien_proxy.policies.base_policy import BasePolicy
from luthien_proxy.policy_core import PolicyContext
from luthien_proxy.policy_core.chunk_builders import create_text_chunk
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )


def _detect_capitalization_pattern(text: str) -> str:
    """Detect the capitalization pattern of a string.

    Returns one of:
    - "upper": all uppercase (e.g., "HELLO")
    - "lower": all lowercase (e.g., "hello")
    - "title": first char uppercase, rest lowercase (e.g., "Hello")
    - "mixed": any other pattern (e.g., "hELLo")
    """
    if not text:
        return "lower"

    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return "lower"

    if all(c.isupper() for c in alpha_chars):
        return "upper"
    if all(c.islower() for c in alpha_chars):
        return "lower"
    if alpha_chars[0].isupper() and all(c.islower() for c in alpha_chars[1:]):
        return "title"
    return "mixed"


def _apply_capitalization_pattern(source: str, replacement: str) -> str:
    """Apply the capitalization pattern from source to replacement.

    For simple patterns (all upper, all lower, title case), applies that pattern.
    For mixed patterns, applies character-by-character where possible,
    falling back to the literal replacement for remaining characters.

    Args:
        source: The original matched text (determines capitalization pattern)
        replacement: The replacement text to transform

    Returns:
        The replacement text with capitalization applied from source
    """
    pattern = _detect_capitalization_pattern(source)

    if pattern == "upper":
        return replacement.upper()
    if pattern == "lower":
        return replacement.lower()
    if pattern == "title":
        return replacement.capitalize()

    # Mixed pattern: apply character-by-character
    result = []
    source_alpha_indices = [i for i, c in enumerate(source) if c.isalpha()]

    # Build a mapping: for each alpha char position in replacement,
    # use the case from the corresponding alpha char in source
    source_cases = [source[i].isupper() for i in source_alpha_indices]

    for i, char in enumerate(replacement):
        if not char.isalpha():
            result.append(char)
            continue

        # Find which alpha position this is in replacement
        alpha_pos = sum(1 for j in range(i) if replacement[j].isalpha())

        if alpha_pos < len(source_cases):
            # Apply case from source
            if source_cases[alpha_pos]:
                result.append(char.upper())
            else:
                result.append(char.lower())
        else:
            # No more source cases to apply, use literal replacement char
            result.append(char)

    return "".join(result)


def apply_replacements(
    text: str,
    replacements: list[tuple[str, str]],
    match_capitalization: bool,
) -> str:
    """Apply all string replacements to the given text.

    Args:
        text: The text to transform
        replacements: List of (from_string, to_string) tuples
        match_capitalization: If True, match case-insensitively and preserve
            the original capitalization pattern in the replacement

    Returns:
        The transformed text with all replacements applied
    """
    if not text or not replacements:
        return text

    result = text

    for from_str, to_str in replacements:
        if not from_str:
            continue

        if match_capitalization:
            # Case-insensitive search with capitalization preservation
            pattern = re.compile(re.escape(from_str), re.IGNORECASE)

            def replace_with_case(match: re.Match) -> str:
                matched_text = match.group(0)
                return _apply_capitalization_pattern(matched_text, to_str)

            result = pattern.sub(replace_with_case, result)
        else:
            # Simple case-sensitive replacement
            result = result.replace(from_str, to_str)

    return result


class StringReplacementPolicy(BasePolicy):
    """Policy that replaces specified strings in response content.

    This policy supports:
    - Multiple string replacements applied in order
    - Case-insensitive matching with capitalization preservation
    - Both streaming and non-streaming responses

    Capitalization preservation (when match_capitalization=True):
    - ALL CAPS source -> ALL CAPS replacement
    - all lower source -> all lower replacement
    - Title Case source -> Title Case replacement
    - MiXeD case source -> character-by-character case matching, falling back
      to literal replacement value for extra characters

    Example: With replacement ("cool", "radicAL") and match_capitalization=True:
    - "cool" -> "radical" (all lowercase)
    - "COOL" -> "RADICAL" (all uppercase)
    - "Cool" -> "Radical" (title case)
    - "cOOl" -> "rADical" (mixed: c->r lower, O->A upper, O->D upper, l->i lower, extra chars literal)
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

    def get_config(self) -> dict:
        """Return the configuration for this policy instance."""
        return {
            "replacements": [[f, t] for f, t in self._replacements],
            "match_capitalization": self._match_capitalization,
        }

    def _apply_replacements(self, text: str) -> str:
        """Apply all configured replacements to the given text."""
        return apply_replacements(text, self._replacements, self._match_capitalization)

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Push non-content chunks immediately; content is handled in on_content_complete."""
        last_chunk: ModelResponse = ctx.last_chunk_received
        if not last_chunk.choices:
            ctx.push_chunk(last_chunk)
            return

        choice = last_chunk.choices[0]
        streaming_choice = cast(StreamingChoices, choice)
        if not hasattr(streaming_choice, "delta") or streaming_choice.delta is None:
            ctx.push_chunk(last_chunk)
            return

        # Content deltas are buffered and emitted in on_content_complete
        if streaming_choice.delta.content is not None:
            return

        # All other chunks (tool calls, finish reasons, etc.) pass through
        ctx.push_chunk(last_chunk)

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """Apply string replacements to the complete content block.

        Waits for the full content block to be accumulated, then applies
        replacements and emits a single chunk with the transformed content.
        This ensures replacements work correctly even if patterns would
        otherwise be split across chunk boundaries.
        """
        stream_state = ctx.original_streaming_response_state
        current_block = stream_state.current_block

        if not isinstance(current_block, ContentStreamBlock):
            return

        original = current_block.content
        if not original:
            return

        transformed = self._apply_replacements(original)

        # Get metadata from a previous chunk for the new chunk
        last_chunk = ctx.last_chunk_received
        chunk = create_text_chunk(
            text=transformed,
            model=last_chunk.model or "unknown",
            response_id=last_chunk.id,
        )
        ctx.push_chunk(chunk)

        if original != transformed:
            ctx.policy_ctx.record_event(
                "policy.string_replacement.content_transformed",
                {
                    "original_length": len(original),
                    "transformed_length": len(transformed),
                    "replacements_count": len(self._replacements),
                },
            )

    async def on_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Apply string replacements to non-streaming response content.

        Args:
            response: Complete ModelResponse from LLM
            context: Policy context

        Returns:
            Response with string replacements applied
        """
        if not response.choices:
            return response

        for choice in response.choices:
            if not (isinstance(choice, Choices) and isinstance(choice.message.content, str)):
                context.record_event(
                    "policy.string_replacement.response_content_warning",
                    {"summary": "Response choice content is not a string, skipping"},
                )
                continue

            original = choice.message.content
            transformed = self._apply_replacements(original)
            choice.message.content = transformed

            if original != transformed:
                context.record_event(
                    "policy.string_replacement.response_content_transformed",
                    {
                        "original_length": len(original),
                        "transformed_length": len(transformed),
                        "replacements_count": len(self._replacements),
                    },
                )
        return response


__all__ = [
    "StringReplacementPolicy",
    "apply_replacements",
]
