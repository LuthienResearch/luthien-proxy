"""StringReplacementPolicy - Replace strings in LLM responses.

This policy replaces specified strings in response content with replacement values.
It supports case-insensitive matching with intelligent capitalization preservation.

Streaming uses a sliding buffer to handle replacements that span chunk boundaries.
The buffer holds back the last N characters (N = longest source length - 1) so that
words split across chunks are still matched and replaced correctly.

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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextDelta,
)
from pydantic import BaseModel, Field

from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
    PolicyContext,
)
from luthien_proxy.policy_core.anthropic_execution_interface import AnthropicPolicyEmission

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicResponse,
    )


@dataclass
class _StreamBufferState:
    """Per-request buffer state for cross-chunk streaming replacements."""

    buffer: str = ""
    last_event_index: int = 0


class StringReplacementConfig(BaseModel):
    """Configuration for StringReplacementPolicy."""

    replacements: list[list[str]] = Field(default_factory=list, description="List of [from, to] string pairs")
    match_capitalization: bool = Field(default=False, description="Match source capitalization pattern")


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
    replacements: Sequence[tuple[str, str]],
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


class StringReplacementPolicy(BasePolicy, AnthropicHookPolicy):
    """Policy that replaces specified strings in response content.

    This policy supports:
    - Multiple string replacements applied in order
    - Case-insensitive matching with capitalization preservation
    - Native Anthropic API responses

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

    def __init__(self, config: StringReplacementConfig | None = None):
        """Initialize with optional config. Accepts dict or Pydantic model."""
        self.config = self._init_config(config, StringReplacementConfig)

        self._replacements: tuple[tuple[str, str], ...] = tuple((pair[0], pair[1]) for pair in self.config.replacements)
        self._match_capitalization = self.config.match_capitalization

        # Buffer size for streaming: hold back enough chars to catch replacements
        # that span chunk boundaries. For sources of length L, we need L-1 chars.
        self._buffer_size: int = max(
            (len(from_str) for from_str, _ in self._replacements),
            default=0,
        )
        self._buffer_size = max(self._buffer_size - 1, 0)

    def _apply_replacements(self, text: str) -> str:
        """Apply all configured replacements to the given text."""
        return apply_replacements(text, self._replacements, self._match_capitalization)

    async def on_anthropic_response(self, response: "AnthropicResponse", context: PolicyContext) -> "AnthropicResponse":
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

    def _get_buffer_state(self, context: PolicyContext) -> _StreamBufferState:
        return context.get_request_state(self, _StreamBufferState, _StreamBufferState)

    def _flush_buffer(self, state: _StreamBufferState) -> list[MessageStreamEvent]:
        """Flush remaining buffer as a final text delta event."""
        if not state.buffer:
            return []
        text = state.buffer
        state.buffer = ""
        flush_delta = TextDelta.model_construct(type="text_delta", text=text)
        return [
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=state.last_event_index,
                delta=flush_delta,
            )
        ]

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        """Transform text_delta events with string replacements.

        Uses a sliding buffer to handle replacements spanning chunk boundaries.
        On each chunk the buffer (post-replacement tail from the prior iteration)
        is prepended to the new raw text, replacements are applied to the combined
        string, and the safe prefix is emitted while the tail is held back.

        The buffer stores post-replacement text, which means replacement results
        can be re-processed on the next iteration. For typical word-level configs
        this is benign (e.g., "goodbye" won't re-match "hello"). Configs where a
        replacement output partially overlaps the same source pattern (e.g.,
        ["ab", "ca"]) may produce different results than full-text processing
        at chunk boundaries.

        A single buffer is shared across content blocks within one request.
        This is safe because the Anthropic protocol sends blocks sequentially
        (not interleaved), and content_block_stop flushes the buffer between blocks.
        """
        # Flush buffer before message_delta so content blocks precede it
        if isinstance(event, RawMessageDeltaEvent) and self._buffer_size > 0:
            state = self._get_buffer_state(context)
            flush_events = self._flush_buffer(state)
            flush_events.append(event)
            return flush_events

        # Flush buffer before content_block_stop so the block is complete
        if isinstance(event, RawContentBlockStopEvent) and self._buffer_size > 0:
            state = self._get_buffer_state(context)
            flush_events = self._flush_buffer(state)
            flush_events.append(event)
            return flush_events

        if not isinstance(event, RawContentBlockDeltaEvent):
            return [event]

        if not isinstance(event.delta, TextDelta):
            return [event]

        # No buffering needed (single-char or empty replacements)
        if self._buffer_size <= 0:
            original = event.delta.text
            transformed = self._apply_replacements(original)
            new_delta = event.delta.model_copy(update={"text": transformed})
            return [event.model_copy(update={"delta": new_delta})]

        # Buffered path: combine buffer + new chunk, apply replacements
        state = self._get_buffer_state(context)
        combined = state.buffer + event.delta.text
        replaced = self._apply_replacements(combined)
        state.last_event_index = event.index

        if len(replaced) <= self._buffer_size:
            # Not enough text to emit safely yet
            state.buffer = replaced
            return []

        # Emit safe prefix, hold back the tail
        emit_text = replaced[: -self._buffer_size]
        state.buffer = replaced[-self._buffer_size :]

        new_delta = event.delta.model_copy(update={"text": emit_text})
        return [event.model_copy(update={"delta": new_delta})]

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        """Safety net: flush buffer if stream ended without message_delta."""
        if self._buffer_size <= 0:
            return []
        state = context.pop_request_state(self, _StreamBufferState)
        if state is None:
            return []
        return list(self._flush_buffer(state))


__all__ = [
    "StringReplacementPolicy",
    "StringReplacementConfig",
    "apply_replacements",
]
