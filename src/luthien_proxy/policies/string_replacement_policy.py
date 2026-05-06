"""StringReplacementPolicy - Replace strings in LLM requests and/or responses.

This policy replaces specified strings in request and/or response content with
replacement values. It supports case-insensitive matching with intelligent
capitalization preservation.

The ``apply_to`` config field selects which side of the conversation to scrub:

- ``"response"`` (default): only model output is transformed. Back-compat default.
- ``"request"``: only the inbound request from the client is transformed.
- ``"both"``: both sides are transformed.

The request-side hook scrubs string content, ``text`` blocks, and ``tool_result``
content (both the string form and the list-of-text-blocks form). It does not
touch ``tool_use``, ``image``, ``thinking``, or the top-level ``system`` field.

Streaming uses a sliding buffer to handle replacements that span chunk
boundaries. The buffer holds back the last N characters
(N = longest source length - 1) so that words split across chunks are still
matched and replaced correctly.

Example config:
    policy:
      class: "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
      config:
        replacements:
          - ["foo", "bar"]
          - ["hello", "goodbye"]
        match_capitalization: true
        apply_to: "both"
"""

from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

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
    """Per-request buffer state for cross-chunk streaming replacements.

    Also accumulates the aggregate counters reported by the
    ``policy.string_replacement.response_modified`` event emitted once at
    stream completion.
    """

    buffer: str = ""
    last_event_index: int = 0
    # Aggregated counters for the response_modified event emitted at stream end.
    total_replacements: int = 0
    original_length: int = 0
    transformed_length: int = 0
    # Block indices that had at least one substitution applied within them.
    modified_block_indices: set[int] = field(default_factory=set)


class StringReplacementConfig(BaseModel):
    """Configuration for StringReplacementPolicy."""

    replacements: list[list[str]] = Field(default_factory=list, description="List of [from, to] string pairs")
    match_capitalization: bool = Field(default=False, description="Match source capitalization pattern")
    apply_to: Literal["request", "response", "both"] = Field(
        default="response",
        description=(
            "Which side(s) of the conversation to apply replacements to. "
            "'response' (default) only modifies model output; 'request' only modifies "
            "incoming user content; 'both' modifies both."
        ),
    )

    @field_validator("replacements")
    @classmethod
    def _validate_replacement_pairs(cls, replacements: list[list[str]]) -> list[list[str]]:
        """Each entry must be a length-2 list of strings ([from, to]).

        Caught at config-load time so misconfigured policies fail loudly rather
        than blowing up on the first request.
        """
        for index, pair in enumerate(replacements):
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError(f"replacements[{index}] must be a [from, to] pair of length 2, got {pair!r}")
            if not all(isinstance(item, str) for item in pair):
                raise ValueError(f"replacements[{index}] must contain two strings, got {pair!r}")
        return replacements


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


def _compile_case_insensitive_patterns(
    replacements: Sequence[tuple[str, str]],
) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Pre-compile case-insensitive regex patterns for ``match_capitalization=True``.

    Returns one ``(pattern, to_str)`` per non-empty source. Empty sources are
    skipped to mirror :func:`apply_replacements_with_count`'s behavior.
    """
    return tuple(
        (re.compile(re.escape(from_str), re.IGNORECASE), to_str) for from_str, to_str in replacements if from_str
    )


def _apply_with_compiled_count(
    text: str,
    compiled: Sequence[tuple[re.Pattern[str], str]],
) -> tuple[str, int]:
    """Apply pre-compiled case-insensitive replacements; return (new_text, count)."""
    if not text or not compiled:
        return text, 0

    result = text
    total = 0
    for pattern, to_str in compiled:

        def replace_with_case(match: re.Match[str], _to_str: str = to_str) -> str:
            return _apply_capitalization_pattern(match.group(0), _to_str)

        result, count = pattern.subn(replace_with_case, result)
        total += count
    return result, total


def apply_replacements_with_count(
    text: str,
    replacements: Sequence[tuple[str, str]],
    match_capitalization: bool,
) -> tuple[str, int]:
    """Apply all string replacements and return (new_text, total_substitutions).

    The substitution count reflects the number of actual substitutions made
    at each step, including replacements that operate on output of an earlier
    step (chained replacements). For example, with replacements
    ``[("foo", "barbar"), ("bar", "y")]`` against ``"foobar"``, this returns
    ``("yyy", 4)``: one ``foo`` -> ``barbar`` substitution followed by three
    ``bar`` -> ``y`` substitutions on the resulting ``"barbarbar"``.
    """
    if not text or not replacements:
        return text, 0

    if match_capitalization:
        return _apply_with_compiled_count(text, _compile_case_insensitive_patterns(replacements))

    result = text
    total = 0
    for from_str, to_str in replacements:
        if not from_str:
            continue
        # Simple case-sensitive replacement; count occurrences before substituting.
        count = result.count(from_str)
        if count:
            result = result.replace(from_str, to_str)
        total += count

    return result, total


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
    transformed, _ = apply_replacements_with_count(text, replacements, match_capitalization)
    return transformed


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
        self._apply_to_request: bool = self.config.apply_to in ("request", "both")
        self._apply_to_response: bool = self.config.apply_to in ("response", "both")
        # Pre-compile case-insensitive patterns once; the streaming hot path
        # would otherwise re-compile per chunk (and per replacement) on every
        # text_delta event.
        self._compiled_patterns: tuple[tuple[re.Pattern[str], str], ...] | None = (
            _compile_case_insensitive_patterns(self._replacements) if self._match_capitalization else None
        )

        # Buffer size for streaming: hold back enough chars to catch replacements
        # that span chunk boundaries. For sources of length L, we need L-1 chars.
        self._buffer_size: int = max(
            (len(from_str) for from_str, _ in self._replacements),
            default=0,
        )
        self._buffer_size = max(self._buffer_size - 1, 0)

    def _apply_replacements_with_count(self, text: str) -> tuple[str, int]:
        """Apply all configured replacements and return (new_text, substitution_count).

        Uses the precompiled patterns when ``match_capitalization=True`` so the
        per-chunk streaming path doesn't re-compile regexes on every event.
        """
        if self._compiled_patterns is not None:
            return _apply_with_compiled_count(text, self._compiled_patterns)
        return apply_replacements_with_count(text, self._replacements, match_capitalization=False)

    async def on_anthropic_request(self, request: "AnthropicRequest", context: PolicyContext) -> "AnthropicRequest":
        """Apply replacements to incoming request messages when ``apply_to`` includes 'request'.

        Targets:
        - String message content (``message["content"]`` when ``str``)
        - Text blocks (``{"type": "text", "text": "..."}``)
        - Tool result blocks with string content
          (``{"type": "tool_result", "content": "..."}``)
        - Tool result blocks with list-of-text-block content
          (``{"type": "tool_result", "content": [{"type": "text", ...}, ...]}``)

        Other block types (``tool_use``, ``image``, ``thinking``, etc.) and the
        top-level ``system`` field are left untouched.

        **Mutation safety:** the policy MUST NOT mutate the input ``request``
        dict or any of its nested values, because the gateway's
        ``_initial_request`` aliases the same object and a shallow copy
        (``dict(self._initial_request)``) is later used to record the
        ``original_request`` history event. Mutating in place would corrupt
        history (this is the regression that took down PR #573). We deep-copy
        ``messages`` once, mutate the copy, and return a new top-level request
        dict referencing the copied list.
        """
        if not self._apply_to_request or not self._replacements:
            return request

        original_messages = request.get("messages")
        if not isinstance(original_messages, list) or not original_messages:
            return request

        # Deep-copy messages once; mutating the copy is safe.
        new_messages = copy.deepcopy(original_messages)

        blocks_modified = 0
        total_replacements = 0
        original_length = 0
        transformed_length = 0

        for message in new_messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                transformed, count = self._apply_replacements_with_count(content)
                if count > 0:
                    blocks_modified += 1
                    total_replacements += count
                    original_length += len(content)
                    transformed_length += len(transformed)
                    message["content"] = transformed
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                stats = self._apply_to_block_in_place(block)
                if stats is None:
                    continue
                count, before_len, after_len = stats
                if count > 0:
                    blocks_modified += 1
                    total_replacements += count
                    original_length += before_len
                    transformed_length += after_len

        if blocks_modified == 0:
            # Nothing changed — return the original request untouched. We
            # discard the deep copy rather than aliasing it back into a new
            # top-level dict, since identity-of-input is the easiest contract
            # to verify in tests when the hook is a true no-op.
            return request

        # Build a new top-level dict so reassigning ``messages`` doesn't mutate
        # the original request dict (which is aliased by ``_initial_request``).
        new_request: dict[str, Any] = dict(request)
        new_request["messages"] = new_messages
        context.record_event(
            "policy.string_replacement.request_modified",
            {
                "blocks_modified": blocks_modified,
                "total_replacements": total_replacements,
                "original_length": original_length,
                "transformed_length": transformed_length,
            },
        )
        return new_request  # type: ignore[return-value]

    def _apply_to_block_in_place(self, block: object) -> tuple[int, int, int] | None:
        """Apply replacements to a single content block in place.

        Returns ``(substitution_count, original_text_length, transformed_text_length)``
        if the block is a recognized text-bearing shape, else ``None`` (block was
        ignored). The lengths reflect only the text actually scrubbed within
        this block.
        """
        if not isinstance(block, dict):
            return None
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str):
                return None
            transformed, count = self._apply_replacements_with_count(text)
            if count > 0:
                block["text"] = transformed
            return (count, len(text), len(transformed))
        if block_type == "tool_result":
            content = block.get("content")
            if isinstance(content, str):
                transformed, count = self._apply_replacements_with_count(content)
                if count > 0:
                    block["content"] = transformed
                return (count, len(content), len(transformed))
            if isinstance(content, list):
                total_count = 0
                total_before = 0
                total_after = 0
                for inner in content:
                    if not isinstance(inner, dict) or inner.get("type") != "text":
                        continue
                    text = inner.get("text")
                    if not isinstance(text, str):
                        continue
                    transformed, count = self._apply_replacements_with_count(text)
                    if count > 0:
                        inner["text"] = transformed
                    total_count += count
                    total_before += len(text)
                    total_after += len(transformed)
                return (total_count, total_before, total_after)
            return None
        return None

    async def on_anthropic_response(self, response: "AnthropicResponse", context: PolicyContext) -> "AnthropicResponse":
        """Transform text content blocks with string replacements.

        Iterates through content blocks and applies replacements to text blocks.
        Tool use, thinking, and other block types remain unchanged. Emits a
        single ``policy.string_replacement.response_modified`` event per
        response if any block was actually modified. No-op when ``apply_to``
        excludes the response side.
        """
        if not self._apply_to_response:
            return response

        blocks_modified = 0
        total_replacements = 0
        original_length = 0
        transformed_length = 0

        for block in response.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                text = block.get("text")
                if isinstance(text, str):
                    transformed, count = self._apply_replacements_with_count(text)
                    block["text"] = transformed

                    if count > 0:
                        blocks_modified += 1
                        total_replacements += count
                        original_length += len(text)
                        transformed_length += len(transformed)

        if blocks_modified > 0:
            context.record_event(
                "policy.string_replacement.response_modified",
                {
                    "blocks_modified": blocks_modified,
                    "total_replacements": total_replacements,
                    "original_length": original_length,
                    "transformed_length": transformed_length,
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
        # The buffer holds post-replacement text — the bytes count toward the
        # transformed total now that they are emitted.
        state.transformed_length += len(text)
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

        No-op (passthrough) when ``apply_to`` excludes the response side.

        Uses a sliding buffer to handle replacements spanning chunk boundaries.
        On each chunk the buffer (post-replacement tail from the prior iteration)
        is prepended to the new raw text, replacements are applied to the combined
        string, and the safe prefix is emitted while the tail is held back.

        The buffer stores post-replacement text, which means replacement results
        can be re-processed on the next iteration. For typical word-level configs
        this is benign (e.g., "goodbye" won't re-match "hello"). Configs where a
        replacement output partially overlaps the same source pattern (e.g.,
        ["ab", "ca"]) may produce different results than full-text processing
        at chunk boundaries. The aggregated ``total_replacements`` metric reports
        substitutions actually performed during streaming; for these overlap-prone
        configs it can diverge from the count produced by running the same
        replacements over the fully-assembled non-streaming response.

        A single buffer is shared across content blocks within one request.
        This is safe because the Anthropic protocol sends blocks sequentially
        (not interleaved), and content_block_stop flushes the buffer between blocks.
        """
        if not self._apply_to_response:
            return [event]

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
            state = self._get_buffer_state(context)
            original = event.delta.text
            transformed, count = self._apply_replacements_with_count(original)
            state.original_length += len(original)
            state.transformed_length += len(transformed)
            if count > 0:
                state.total_replacements += count
                state.modified_block_indices.add(event.index)
            new_delta = event.delta.model_copy(update={"text": transformed})
            return [event.model_copy(update={"delta": new_delta})]

        # Buffered path: combine buffer + new chunk, apply replacements.
        state = self._get_buffer_state(context)
        original_chunk = event.delta.text
        combined = state.buffer + original_chunk
        replaced, count = self._apply_replacements_with_count(combined)
        state.original_length += len(original_chunk)
        if count > 0:
            state.total_replacements += count
            state.modified_block_indices.add(event.index)
        state.last_event_index = event.index

        if len(replaced) <= self._buffer_size:
            # Not enough text to emit safely yet — defer the bytes (and their
            # contribution to transformed_length) until they are flushed.
            state.buffer = replaced
            return []

        # Emit safe prefix, hold back the tail.
        emit_text = replaced[: -self._buffer_size]
        state.buffer = replaced[-self._buffer_size :]
        state.transformed_length += len(emit_text)

        new_delta = event.delta.model_copy(update={"text": emit_text})
        return [event.model_copy(update={"delta": new_delta})]

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        """Flush any remaining buffer and emit the aggregate response_modified event.

        Emits a single ``policy.string_replacement.response_modified`` event
        summarizing all substitutions made during the stream, mirroring the
        non-streaming path. Skipped when no substitutions were made or when
        ``apply_to`` excludes the response side.
        """
        if not self._apply_to_response:
            return []
        state = context.pop_request_state(self, _StreamBufferState)
        if state is None:
            return []
        flushed: list[AnthropicPolicyEmission] = list(self._flush_buffer(state))
        if state.total_replacements > 0:
            context.record_event(
                "policy.string_replacement.response_modified",
                {
                    "blocks_modified": len(state.modified_block_indices),
                    "total_replacements": state.total_replacements,
                    "original_length": state.original_length,
                    "transformed_length": state.transformed_length,
                },
            )
        return flushed


__all__ = [
    "StringReplacementPolicy",
    "StringReplacementConfig",
    "apply_replacements",
]
