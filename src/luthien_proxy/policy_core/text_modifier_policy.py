"""Base class for policies that modify text content in Anthropic responses.

Subclasses override one or two methods:
- modify_text(text) -> text: transform text content in-place
- extra_text() -> str | None: append additional text after all content

The base class handles all format-specific plumbing across 2 code paths:
Anthropic non-streaming and Anthropic streaming.

When extra_text is used and the response contains tool_use blocks, the text
is appended to the last text block before any tool_use — preserving the
Anthropic API invariant that text blocks must precede tool_use blocks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextDelta,
    ToolUseBlock,
)

from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
    BasePolicy,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext


@dataclass
class _StreamState:
    """Per-policy, per-request streaming state for TextModifierPolicy hook methods."""

    last_text_index: int = field(default=-1)
    held_stop: MessageStreamEvent | None = field(default=None)
    extra_text_emitted: bool = field(default=False)


class TextModifierPolicy(BasePolicy, AnthropicExecutionInterface):
    """Base class for policies that modify text content in Anthropic responses.

    Override modify_text() to transform text in-place across streaming and non-streaming.
    Override extra_text() to append content after all response text.
    Both are optional — the base class passes through unchanged by default.

    Tool calls, thinking blocks, and images are always passed through unchanged.
    """

    def modify_text(self, text: str) -> str:
        """Transform response text. Default: passthrough."""
        return text

    def extra_text(self) -> str | None:
        """Return text to append after all content, or None. Default: None."""
        return None

    # -- Anthropic execution ---------------------------------------------------

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: PolicyContext
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Apply modify_text and extra_text across Anthropic streaming and non-streaming."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            request = io.request

            if request.get("stream", False):
                suffix = self.extra_text()
                last_text_index = -1
                held_stop: MessageStreamEvent | None = None

                async for event in io.stream(request):
                    if isinstance(event, RawContentBlockStartEvent):
                        if not isinstance(event.content_block, ToolUseBlock):
                            last_text_index = event.index

                    if isinstance(event, RawContentBlockStopEvent) and event.index == last_text_index:
                        if held_stop is not None:
                            yield held_stop
                        held_stop = event
                        continue

                    if (
                        isinstance(event, RawContentBlockStartEvent)
                        and isinstance(event.content_block, ToolUseBlock)
                        and suffix is not None
                        and held_stop is not None
                    ):
                        yield RawContentBlockDeltaEvent(
                            type="content_block_delta",
                            index=last_text_index,
                            delta=TextDelta(type="text_delta", text=suffix),
                        )
                        yield held_stop
                        held_stop = None
                        suffix = None

                    if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
                        new_delta = event.delta.model_copy(update={"text": self.modify_text(event.delta.text)})
                        yield event.model_copy(update={"delta": new_delta})
                    else:
                        yield event

                if suffix is not None and held_stop is not None:
                    yield RawContentBlockDeltaEvent(
                        type="content_block_delta",
                        index=last_text_index,
                        delta=TextDelta(type="text_delta", text=suffix),
                    )
                    yield held_stop
                elif held_stop is not None:
                    yield held_stop
                return

            # Non-streaming
            response = await io.complete(request)
            self._modify_anthropic_response(response)
            yield response

        return _run()

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

    # -- Anthropic hook interface (for composition via MultiSerialPolicy) -------

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
                    return [suffix_delta, state.held_stop, event]

            if not isinstance(event.content_block, ToolUseBlock):
                state.last_text_index = event.index
            return [event]

        # Hold back content_block_stop for text blocks
        if isinstance(event, RawContentBlockStopEvent) and event.index == state.last_text_index:
            result: list[MessageStreamEvent] = []
            if state.held_stop is not None:
                result.append(state.held_stop)
            state.held_stop = event
            return result

        if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
            new_delta = event.delta.model_copy(update={"text": self.modify_text(event.delta.text)})
            return [event.model_copy(update={"delta": new_delta})]
        return [event]

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        """Flush held stop and inject suffix if no tool_use was seen."""
        state = context.get_request_state(self, _StreamState, _StreamState)
        if state.held_stop is None:
            return []

        result: list[AnthropicPolicyEmission] = []
        suffix = self.extra_text()
        if suffix is not None and not state.extra_text_emitted:
            result.append(
                RawContentBlockDeltaEvent(
                    type="content_block_delta",
                    index=state.last_text_index,
                    delta=TextDelta(type="text_delta", text=suffix),
                )
            )
        result.append(state.held_stop)
        return result


__all__ = ["TextModifierPolicy"]
