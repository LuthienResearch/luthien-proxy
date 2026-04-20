"""StringReplacementPolicy - Replace strings in LLM requests and/or responses.

This policy replaces specified strings in message content with replacement values.
It supports case-insensitive matching with intelligent capitalization preservation.

By default (``apply_to="response"``), only response content is modified — this
preserves backward compatibility with existing configurations. Set ``apply_to``
to ``"request"`` or ``"both"`` deliberately when you need to strip patterns from
incoming messages (e.g., prompt injection patterns in tool results).

Streaming uses a sliding buffer to handle replacements that span chunk boundaries.
The buffer holds back the last N characters (N = longest source length - 1) so that
words split across chunks are still matched and replaced correctly.

Example config (response-only, default):
    policy:
      class: "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
      config:
        replacements:
          - ["foo", "bar"]
          - ["hello", "goodbye"]
        match_capitalization: true

Example config (request-side filtering for prompt injection defense):
    policy:
      class: "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
      config:
        apply_to: "both"   # "request", "response", or "both"
        replacements:
          - ["<system_warning>", "[STRIPPED]"]
          - ["ignore previous instructions", "[STRIPPED]"]
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextDelta,
)
from pydantic import BaseModel, Field, field_validator

from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
    PolicyContext,
)
from luthien_proxy.policy_core.anthropic_execution_interface import AnthropicPolicyEmission

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
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
    apply_to: Literal["request", "response", "both"] = Field(
        default="response",
        description=(
            "Which side of the conversation to apply replacements to. "
            "'response' (default) only modifies response content — safe for existing configs. "
            "'request' only modifies incoming request messages (useful for stripping prompt injection patterns). "
            "'both' applies replacements to both request messages and response content. "
            "Changing from 'response' to 'request' or 'both' is a deliberate choice that affects "
            "what the model sees — review your replacement patterns before enabling."
        ),
    )

    @field_validator("replacements")
    @classmethod
    def _validate_replacement_pairs(cls, v: list[list[str]]) -> list[list[str]]:
        for i, pair in enumerate(v):
            if len(pair) != 2:
                raise ValueError(
                    f"replacements[{i}] must be a [from, to] pair with exactly 2 elements, got {len(pair)}"
                )
            if not pair[0]:
                raise ValueError(f"replacements[{i}]: the 'from' string (first element) must not be empty")
        return v


def _detect_capitalization_pattern(text: str) -> str:
    """Detect the capitalization pattern of a string.

    Returns one of:
    - "upper": all uppercase (e.g., "HELLO")
    - "lower": all lowercase (e.g., "hello")
    - "title": first letter uppercase, rest lowercase (e.g., "Hello")
    - "mixed": any other pattern (e.g., "hELLO")
    """
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
    """Apply the capitalization pattern of source to replacement.

    Args:
        source: The matched text whose capitalization pattern to use
        replacement: The replacement text to apply the pattern to

    Returns:
        The replacement text with the source's capitalization pattern applied
    """
    pattern = _detect_capitalization_pattern(source)

    if pattern == "upper":
        return replacement.upper()
    elif pattern == "lower":
        return replacement.lower()
    elif pattern == "title":
        return replacement.capitalize()
    else:
        # Mixed: apply character-by-character case matching
        source_alpha = [c for c in source if c.isalpha()]
        replacement_chars = list(replacement)

        alpha_idx = 0
        for i, char in enumerate(replacement_chars):
            if char.isalpha() and alpha_idx < len(source_alpha):
                if source_alpha[alpha_idx].isupper():
                    replacement_chars[i] = char.upper()
                else:
                    replacement_chars[i] = char.lower()
                alpha_idx += 1
        return "".join(replacement_chars)


def apply_replacements(
    text: str,
    replacements: Sequence[tuple[str, str]],
    match_capitalization: bool = False,
) -> str:
    """Apply a sequence of string replacements to text.

    Args:
        text: The text to apply replacements to
        replacements: Sequence of (from, to) string pairs
        match_capitalization: If True, preserve the capitalization pattern of matched text

    Returns:
        The text with all replacements applied in order
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


def _apply_replacements_to_request_messages(
    request: "AnthropicRequest",
    replacements: tuple[tuple[str, str], ...],
    match_capitalization: bool,
) -> tuple[int, int]:
    """Apply replacements to all text content in request messages in-place.

    Iterates over all messages and their content blocks, applying replacements to:
    - String message content
    - Text blocks (type="text")
    - Tool result content (string or list of text blocks)

    Tool use blocks (type="tool_use") are left unchanged — their ``input`` field
    is structured JSON, not free text, and modifying it could break tool calls.

    Args:
        request: The Anthropic request dict to modify in-place
        replacements: Tuple of (from, to) string pairs
        match_capitalization: Whether to preserve capitalization patterns

    Returns:
        Tuple of (blocks_modified, total_replacements) counts
    """
    blocks_modified = 0
    total_replacements = 0

    for message in request.get("messages", []):
        content = message.get("content")
        if content is None:
            continue

        if isinstance(content, str):
            replaced = apply_replacements(content, replacements, match_capitalization)
            if replaced != content:
                message["content"] = replaced
                blocks_modified += 1
                # Count replacements: difference in length / avg replacement delta is imprecise,
                # so we count occurrences of each pattern instead
                for from_str, _ in replacements:
                    if not from_str:
                        continue
                    if match_capitalization:
                        total_replacements += len(re.findall(re.escape(from_str), content, re.IGNORECASE))
                    else:
                        total_replacements += content.count(from_str)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type")

                if block_type == "text":
                    text = block.get("text", "")
                    if isinstance(text, str):
                        replaced = apply_replacements(text, replacements, match_capitalization)
                        if replaced != text:
                            block["text"] = replaced  # type: ignore[typeddict-unknown-key]
                            blocks_modified += 1
                            for from_str, _ in replacements:
                                if not from_str:
                                    continue
                                if match_capitalization:
                                    total_replacements += len(re.findall(re.escape(from_str), text, re.IGNORECASE))
                                else:
                                    total_replacements += text.count(from_str)

                elif block_type == "tool_result":
                    tool_content = block.get("content")
                    if isinstance(tool_content, str):
                        replaced = apply_replacements(tool_content, replacements, match_capitalization)
                        if replaced != tool_content:
                            block["content"] = replaced  # type: ignore[typeddict-unknown-key]
                            blocks_modified += 1
                            for from_str, _ in replacements:
                                if not from_str:
                                    continue
                                if match_capitalization:
                                    total_replacements += len(
                                        re.findall(re.escape(from_str), tool_content, re.IGNORECASE)
                                    )
                                else:
                                    total_replacements += tool_content.count(from_str)
                    elif isinstance(tool_content, list):
                        for inner_block in tool_content:
                            if not isinstance(inner_block, dict):
                                continue
                            if inner_block.get("type") == "text":
                                text = inner_block.get("text", "")
                                if isinstance(text, str):
                                    replaced = apply_replacements(text, replacements, match_capitalization)
                                    if replaced != text:
                                        inner_block["text"] = replaced  # type: ignore[typeddict-unknown-key]
                                        blocks_modified += 1
                                        for from_str, _ in replacements:
                                            if not from_str:
                                                continue
                                            if match_capitalization:
                                                total_replacements += len(
                                                    re.findall(re.escape(from_str), text, re.IGNORECASE)
                                                )
                                            else:
                                                total_replacements += text.count(from_str)

    return blocks_modified, total_replacements


class StringReplacementPolicy(BasePolicy, AnthropicHookPolicy):
    """Policy that replaces specified strings in request and/or response content.

    This policy supports:
    - Multiple string replacements applied in order
    - Case-insensitive matching with capitalization preservation
    - Request-side filtering (for prompt injection defense)
    - Response-side filtering (default behavior)
    - Native Anthropic API responses and streaming

    The ``apply_to`` config controls which side is filtered:
    - ``"response"`` (default): only response content is modified. Safe for existing configs.
    - ``"request"``: only incoming request messages are modified. Useful for stripping
      prompt injection patterns embedded in tool results before they reach the model.
    - ``"both"``: both request messages and response content are modified.

    Changing ``apply_to`` from ``"response"`` to ``"request"`` or ``"both"`` is a
    deliberate choice — it affects what the model sees, not just what the client sees.

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

    Dashboard observability (via ``context.record_event``):
    - ``policy.string_replacement.request_modified`` is recorded when request content is
      modified, with ``blocks_modified``, ``total_replacements``, and ``apply_to`` fields.
    - ``policy.string_replacement.response_modified`` is recorded when response content is
      modified (includes ``session_id`` automatically).
    """

    def __init__(self, config: StringReplacementConfig | None = None):
        """Initialize with optional config. Accepts dict or Pydantic model."""
        self.config = self._init_config(config, StringReplacementConfig)

        self._replacements: tuple[tuple[str, str], ...] = tuple((pair[0], pair[1]) for pair in self.config.replacements)
        self._match_capitalization = self.config.match_capitalization
        self._apply_to = self.config.apply_to

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

    async def on_anthropic_request(self, request: "AnthropicRequest", context: PolicyContext) -> "AnthropicRequest":
        """Transform request messages with string replacements when apply_to includes 'request'.

        Iterates over all messages and applies replacements to:
        - String message content
        - Text content blocks
        - Tool result content (string or list of text blocks)

        Tool use blocks are left unchanged to avoid breaking structured tool inputs.

        When content is modified, records an event via ``context.record_event`` with
        intervention counts for dashboard observability. No event is recorded when no
        patterns match.
        """
        if self._apply_to not in ("request", "both"):
            return request

        blocks_modified, total_replacements = _apply_replacements_to_request_messages(
            request, self._replacements, self._match_capitalization
        )

        if blocks_modified > 0:
            context.record_event(
                "policy.string_replacement.request_modified",
                {
                    "blocks_modified": blocks_modified,
                    "total_replacements": total_replacements,
                    "apply_to": self._apply_to,
                },
            )

        return request

    async def on_anthropic_response(self, response: "AnthropicResponse", context: PolicyContext) -> "AnthropicResponse":
        """Transform text content blocks with string replacements.

        Iterates through content blocks and applies replacements to text blocks.
        Tool use, thinking, and other block types remain unchanged.

        Only runs when apply_to is 'response' or 'both'.
        """
        if self._apply_to not in ("response", "both"):
            return response

        for block in response.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                text = block.get("text")
                if isinstance(text, str):
                    original = text
                    transformed = self._apply_replacements(text)
                    block["text"] = transformed

                    if original != transformed:
                        context.record_event(
                            "policy.string_replacement.response_modified",
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

        Only runs when apply_to is 'response' or 'both'.
        """
        if self._apply_to not in ("response", "both"):
            return [event]

        # Flush buffer before message_delta so content blocks precede it
        if isinstance(event, RawMessageDeltaEvent) and self._buffer_size > 0:
            state = self._get_buffer_state(context)
            flush_events = self._flush_buffer(state)
            return [*flush_events, event]

        # Flush buffer on content_block_stop
        if isinstance(event, RawContentBlockStopEvent) and self._buffer_size > 0:
            state = self._get_buffer_state(context)
            flush_events = self._flush_buffer(state)
            return [*flush_events, event]

        if not isinstance(event, RawContentBlockDeltaEvent):
            return [event]
        if not isinstance(event.delta, TextDelta):
            return [event]

        raw_text = event.delta.text
        if self._buffer_size <= 0:
            # No buffering needed — apply replacements directly
            replaced = self._apply_replacements(raw_text)
            new_delta = event.delta.model_copy(update={"text": replaced})
            return [event.model_copy(update={"delta": new_delta})]

        state = self._get_buffer_state(context)
        combined = state.buffer + raw_text
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
