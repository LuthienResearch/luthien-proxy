"""DeAIPolicy - Rewrite AI-generated text to sound naturally human.

Intercepts LLM response text blocks and rewrites them using a secondary LLM
call to remove common AI writing patterns. Uses paragraph-chunked streaming
so text is humanized incrementally as it arrives, supporting indefinite-length
responses without truncation.

Example config:
    policy:
      class: "luthien_proxy.policies.deai_policy:DeAIPolicy"
      config:
        model: "claude-haiku-4-5"
        temperature: 0.7
        chunk_size: 500
        context_overlap: 200
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextDelta,
)

from luthien_proxy.llm.types.anthropic import AnthropicTextBlock
from luthien_proxy.policies.deai_utils import (
    DeAIConfig,
    call_deai_chunk,
    find_chunk_boundary,
    split_into_chunks,
)
from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
)
from luthien_proxy.policy_core.anthropic_execution_interface import AnthropicPolicyEmission
from luthien_proxy.settings import get_settings

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


@dataclass
class _DeAIStreamState:
    buffer: str = ""
    previous_humanized_tail: str = ""
    last_event_index: int = 0
    text_block_indices: set[int] = field(default_factory=set)
    total_chunks: int = 0
    total_chars_in: int = 0
    total_chars_out: int = 0


class DeAIPolicy(BasePolicy, AnthropicHookPolicy):
    """Policy that rewrites AI-generated text to sound more natural.

    Accumulates streaming text deltas into a buffer, splits at paragraph
    boundaries, and humanizes each chunk independently via a secondary LLM
    call. Supports indefinite-length responses without truncation.

    Non-streaming responses are split into chunks and humanized the same way.
    On DeAI failure for any chunk, the original text is emitted.
    """

    @property
    def short_policy_name(self) -> str:
        """Policy display name."""
        return "DeAI"

    def __init__(self, config: DeAIConfig | None = None):
        """Initialize with DeAI config."""
        parsed = self._init_config(config, DeAIConfig)

        settings = get_settings()
        overrides: dict[str, object] = {}
        if settings.llm_judge_model:
            overrides["model"] = settings.llm_judge_model
        if settings.llm_judge_api_base:
            overrides["api_base"] = settings.llm_judge_api_base
        self._config = parsed.model_copy(update=overrides) if overrides else parsed
        self._fallback_api_key = settings.llm_judge_api_key or settings.litellm_master_key or None

    def _state(self, context: PolicyContext) -> _DeAIStreamState:
        return context.get_request_state(self, _DeAIStreamState, _DeAIStreamState)

    def _resolved_api_key(self, context: PolicyContext) -> str | None:
        return self._resolve_judge_api_key(context, self._config.api_key, self._fallback_api_key)

    # ========================================================================
    # Non-streaming path
    # ========================================================================

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        """Humanize text blocks by splitting into chunks."""
        content = response.get("content", [])
        for i, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text", "")
            if not isinstance(text, str) or len(text) < self._config.min_text_length:
                continue

            humanized = await self._humanize_full_text(text, context)
            text_block: AnthropicTextBlock = {"type": "text", "text": humanized}
            content[i] = text_block

        return response

    async def _humanize_full_text(self, text: str, context: PolicyContext) -> str:
        """Split text into chunks, humanize each with context overlap."""
        chunks = split_into_chunks(text, self._config.chunk_size, self._config.force_chunk_size)
        result_parts: list[str] = []
        previous_tail = ""

        for i, chunk in enumerate(chunks):
            is_final = i == len(chunks) - 1
            try:
                humanized = await call_deai_chunk(
                    chunk,
                    self._config,
                    previous_context=previous_tail,
                    is_final=is_final,
                    api_key=self._resolved_api_key(context),
                )
                result_parts.append(humanized)
                if self._config.context_overlap > 0:
                    previous_tail = humanized[-self._config.context_overlap :]
            except Exception as exc:
                logger.error("DeAI chunk %d failed, using original: %s", i, exc)
                result_parts.append(chunk)
                if self._config.context_overlap > 0:
                    previous_tail = chunk[-self._config.context_overlap :]

        result = "".join(result_parts)
        context.record_event(
            "policy.deai.rewritten",
            {
                "summary": f"Humanized {len(text)} chars → {len(result)} chars ({len(chunks)} chunks)",
                "original_length": len(text),
                "humanized_length": len(result),
                "chunks": len(chunks),
            },
        )
        return result

    # ========================================================================
    # Streaming path
    # ========================================================================

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        """Process streaming events with paragraph-chunked humanization."""
        if isinstance(event, RawContentBlockStartEvent):
            cb = event.content_block
            if hasattr(cb, "type") and cb.type == "text":
                state = self._state(context)
                state.text_block_indices.add(event.index)
            return [event]

        if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
            return await self._handle_text_delta(event, context)

        if isinstance(event, RawContentBlockStopEvent):
            return await self._handle_block_stop(event, context)

        # Flush before message_delta (protocol requirement).
        # Safe: buffer is only written by _handle_text_delta (TextDelta events).
        if isinstance(event, RawMessageDeltaEvent):
            state = self._state(context)
            flush = await self._flush_buffer(state, context, is_final=True)
            flush.append(cast(MessageStreamEvent, event))
            return flush

        return [event]

    async def _handle_text_delta(
        self, event: RawContentBlockDeltaEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        state = self._state(context)
        new_text = event.delta.text  # type: ignore[union-attr]
        state.buffer += new_text
        state.last_event_index = event.index

        # Try to extract and humanize a chunk
        events: list[MessageStreamEvent] = []
        while True:
            extracted = self._try_extract_chunk(state)
            if extracted is None:
                break
            chunk, remaining = extracted
            state.buffer = remaining
            humanized = await self._humanize_stream_chunk(chunk, state, context)
            events.extend(self._emit_text_delta(humanized, state.last_event_index))

        return events

    async def _handle_block_stop(
        self, event: RawContentBlockStopEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        state = self._state(context)

        # Only flush buffer for text blocks
        if event.index not in state.text_block_indices:
            return [cast(MessageStreamEvent, event)]

        events = await self._flush_buffer(state, context, is_final=True)
        events.append(cast(MessageStreamEvent, event))
        self._record_stream_summary(state, context)
        # Reset for potential subsequent text blocks in the same response
        state.buffer = ""
        state.previous_humanized_tail = ""
        return events

    async def _flush_buffer(
        self,
        state: _DeAIStreamState,
        context: PolicyContext,
        *,
        is_final: bool = False,
    ) -> list[MessageStreamEvent]:
        if not state.buffer:
            return []
        chunk = state.buffer
        state.buffer = ""
        humanized = await self._humanize_stream_chunk(chunk, state, context, is_final=is_final)
        return self._emit_text_delta(humanized, state.last_event_index)

    def _try_extract_chunk(self, state: _DeAIStreamState) -> tuple[str, str] | None:
        """Try to extract a ready chunk from the buffer.

        Returns (chunk, remaining) or None if not enough text yet.
        """
        boundary = find_chunk_boundary(state.buffer, self._config.chunk_size, self._config.force_chunk_size)
        if boundary is None:
            return None
        return (state.buffer[:boundary], state.buffer[boundary:])

    async def _humanize_stream_chunk(
        self,
        chunk: str,
        state: _DeAIStreamState,
        context: PolicyContext,
        *,
        is_final: bool = False,
    ) -> str:
        """Humanize a chunk, updating state. Falls back to original on error."""
        if len(chunk) < self._config.min_text_length:
            return chunk

        state.total_chars_in += len(chunk)
        state.total_chunks += 1

        try:
            humanized = await call_deai_chunk(
                chunk,
                self._config,
                previous_context=state.previous_humanized_tail,
                is_final=is_final,
                api_key=self._resolved_api_key(context),
            )
        except Exception as exc:
            logger.error("DeAI chunk %d failed, using original: %s", state.total_chunks, exc)
            context.record_event(
                "policy.deai.chunk_error",
                {"summary": f"Chunk {state.total_chunks} failed: {exc}", "error": str(exc)},
            )
            humanized = chunk

        state.total_chars_out += len(humanized)
        if self._config.context_overlap > 0:
            state.previous_humanized_tail = humanized[-self._config.context_overlap :]
        return humanized

    def _emit_text_delta(self, text: str, index: int) -> list[MessageStreamEvent]:
        delta = TextDelta.model_construct(type="text_delta", text=text)
        return [
            cast(
                MessageStreamEvent,
                RawContentBlockDeltaEvent.model_construct(type="content_block_delta", index=index, delta=delta),
            )
        ]

    def _record_stream_summary(self, state: _DeAIStreamState, context: PolicyContext) -> None:
        if state.total_chunks > 0:
            context.record_event(
                "policy.deai.rewritten",
                {
                    "summary": (
                        f"Humanized {state.total_chars_in} chars → {state.total_chars_out} chars "
                        f"({state.total_chunks} chunks)"
                    ),
                    "original_length": state.total_chars_in,
                    "humanized_length": state.total_chars_out,
                    "chunks": state.total_chunks,
                },
            )

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        """Safety net: flush if stream ended without stop/message_delta."""
        state = context.pop_request_state(self, _DeAIStreamState)
        if state is None or not state.buffer:
            return []
        chunk = state.buffer
        state.buffer = ""
        humanized = await self._humanize_stream_chunk(chunk, state, context, is_final=True)
        self._record_stream_summary(state, context)
        return list(self._emit_text_delta(humanized, state.last_event_index))


__all__ = ["DeAIPolicy"]
