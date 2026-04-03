"""Base class for policies that modify text content in Anthropic responses.

Subclasses override one or two methods:
- modify_text(text) -> text: transform text content in-place
- extra_text() -> str | None: append additional text to the last text block

The base class handles all format-specific plumbing for both streaming
and non-streaming Anthropic responses via lifecycle hooks.

When extra_text is used and the response contains tool_use blocks, the text
is appended to the last text block before any tool_use — preserving the
Anthropic API invariant that text blocks must precede tool_use blocks.

If the response contains no text blocks at all (e.g. tool_use only), the
extra_text suffix is dropped and an error is logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)

from luthien_proxy.policy_core import (
    AnthropicPolicyEmission,
    BasePolicy,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


@dataclass
class _StreamState:
    """Per-policy, per-request streaming state for TextModifierPolicy hook methods."""

    last_text_index: int = field(default=-1)
    held_stop: MessageStreamEvent | None = field(default=None)
    extra_text_emitted: bool = field(default=False)


class TextModifierPolicy(BasePolicy):
    """Base class for policies that modify text content in Anthropic responses.

    Satisfies AnthropicExecutionInterface structurally via the four lifecycle hooks.

    Override modify_text() to transform text in-place across streaming and non-streaming.
    Override extra_text() to append to the last text block in the response.
    Both are optional — the base class passes through unchanged by default.

    If a response has no text blocks (e.g. tool_use only), extra_text is
    dropped and an error is logged. Tool calls, thinking blocks, and images
    are always passed through unchanged.
    """

    def modify_text(self, text: str) -> str:
        """Transform response text. Default: passthrough."""
        return text

    def extra_text(self) -> str | None:
        """Return text to append to the last text block, or None. Default: None."""
        return None

    def _modify_anthropic_response(self, response: AnthropicResponse) -> None:
        """Apply modify_text to text blocks and append extra_text to the last one."""
        content = response.get("content", [])

        last_text_block = None
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                text = block.get("text")
                if isinstance(text, str):
                    block["text"] = self.modify_text(text)
                    last_text_block = block

        suffix = self.extra_text()
        if suffix is not None and last_text_block is not None:
            last_text_block["text"] += suffix
        elif suffix is not None:
            logger.error(
                "%s.extra_text() returned content but response had no text blocks — "
                "suffix dropped. Content types present: %s",
                type(self).__name__,
                [b.get("type") if isinstance(b, dict) else type(b).__name__ for b in content],
            )

    # -- Anthropic lifecycle hooks ------------------------------------------------

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        """Pass through request unchanged."""
        return request

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        """Apply modify_text and extra_text to the non-streaming response."""
        self._modify_anthropic_response(response)
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        """Modify text deltas in-stream; hold back text block stops for suffix injection."""
        state = context.get_request_state(self, _StreamState, _StreamState)

        if isinstance(event, RawContentBlockStartEvent):
            # Tool_use block starting — flush suffix into the held text block
            if (
                isinstance(event.content_block, ToolUseBlock)
                and not state.extra_text_emitted
                and state.held_stop is not None
            ):
                suffix = self.extra_text()
                if suffix is not None:
                    state.extra_text_emitted = True
                    suffix_delta = RawContentBlockDeltaEvent(
                        type="content_block_delta",
                        index=state.last_text_index,
                        delta=TextDelta(type="text_delta", text=suffix),
                    )
                    held = state.held_stop
                    state.held_stop = None
                    return [suffix_delta, held, event]

            if isinstance(event.content_block, TextBlock):
                state.last_text_index = event.index
            return [event]

        # Only hold back text block stops when extra_text might need injection
        if (
            isinstance(event, RawContentBlockStopEvent)
            and event.index == state.last_text_index
            and self.extra_text() is not None
            and not state.extra_text_emitted
        ):
            result: list[MessageStreamEvent] = []
            if state.held_stop is not None:
                result.append(state.held_stop)
            state.held_stop = event
            return result

        # Flush suffix + held stop before message_delta to preserve protocol ordering.
        # Content blocks must precede message_delta; emitting them in
        # on_anthropic_stream_complete would place them after message_delta/message_stop.
        if isinstance(event, RawMessageDeltaEvent):
            return self._flush_before_message_delta(state, event)

        if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
            new_delta = event.delta.model_copy(update={"text": self.modify_text(event.delta.text)})
            return [event.model_copy(update={"delta": new_delta})]
        return [event]

    def _flush_before_message_delta(
        self, state: _StreamState, message_delta_event: MessageStreamEvent
    ) -> list[MessageStreamEvent]:
        """Emit pending suffix + held stop before the message_delta event.

        Content blocks must precede message_delta in the Anthropic streaming
        protocol. This method is called from on_anthropic_stream_event when
        a RawMessageDeltaEvent arrives.
        """
        if state.held_stop is None:
            return [message_delta_event]

        result: list[MessageStreamEvent] = []
        suffix = self.extra_text()
        if suffix is not None and not state.extra_text_emitted:
            state.extra_text_emitted = True
            result.append(
                RawContentBlockDeltaEvent(
                    type="content_block_delta",
                    index=state.last_text_index,
                    delta=TextDelta(type="text_delta", text=suffix),
                )
            )
        result.append(state.held_stop)
        state.held_stop = None
        result.append(message_delta_event)
        return result

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        """Safety net: flush anything not already flushed by _flush_before_message_delta.

        Normally _flush_before_message_delta handles suffix injection when the
        message_delta event arrives. This method only emits events if the stream
        ended without a message_delta (e.g. abrupt disconnection).
        """
        state = context.get_request_state(self, _StreamState, _StreamState)
        if state.held_stop is None:
            suffix = self.extra_text()
            if suffix is not None and not state.extra_text_emitted:
                logger.error(
                    "%s.extra_text() returned content but response had no text blocks — "
                    "suffix dropped. Stream contained only non-text content blocks.",
                    type(self).__name__,
                )
            return []

        # Stream ended without message_delta — flush remaining events.
        result: list[AnthropicPolicyEmission] = []
        suffix = self.extra_text()
        if suffix is not None and not state.extra_text_emitted:
            state.extra_text_emitted = True
            result.append(
                RawContentBlockDeltaEvent(
                    type="content_block_delta",
                    index=state.last_text_index,
                    delta=TextDelta(type="text_delta", text=suffix),
                )
            )
        result.append(state.held_stop)
        state.held_stop = None
        return result


__all__ = ["TextModifierPolicy"]
