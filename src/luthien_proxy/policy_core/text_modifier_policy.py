"""Base class for policies that modify text content in Anthropic responses.

Subclasses override one or two methods:
- modify_text(text) -> text: transform text content in-place
- extra_text() -> str | None: append additional text after all content

The base class handles all format-specific plumbing across 2 code paths:
Anthropic non-streaming and Anthropic streaming.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextBlock,
    TextDelta,
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

    max_index: int = field(default=-1)


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
                max_index = -1
                async for event in io.stream(request):
                    if isinstance(event, RawContentBlockStartEvent):
                        max_index = max(max_index, event.index)

                    if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
                        new_delta = event.delta.model_copy(update={"text": self.modify_text(event.delta.text)})
                        yield event.model_copy(update={"delta": new_delta})
                    else:
                        yield event

                suffix = self.extra_text()
                if suffix is not None:
                    new_index = max_index + 1
                    yield cast(
                        MessageStreamEvent,
                        RawContentBlockStartEvent(
                            type="content_block_start",
                            index=new_index,
                            content_block=TextBlock(type="text", text=""),
                        ),
                    )
                    yield cast(
                        MessageStreamEvent,
                        RawContentBlockDeltaEvent(
                            type="content_block_delta",
                            index=new_index,
                            delta=TextDelta(type="text_delta", text=suffix),
                        ),
                    )
                    yield cast(
                        MessageStreamEvent,
                        RawContentBlockStopEvent(
                            type="content_block_stop",
                            index=new_index,
                        ),
                    )
                return

            # Non-streaming
            response = await io.complete(request)
            self._modify_anthropic_response(response)
            yield response

        return _run()

    def _modify_anthropic_response(self, response: AnthropicResponse) -> None:
        """Apply modify_text to text blocks and append extra_text if present."""
        content = response.get("content", [])

        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                text = block.get("text")
                if isinstance(text, str):
                    block["text"] = self.modify_text(text)

        suffix = self.extra_text()
        if suffix is not None:
            new_content = list(content)
            new_content.append({"type": "text", "text": suffix})
            response["content"] = new_content  # type: ignore[typeddict-item]

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
        """Modify text deltas in-stream; track the max content block index for extra_text."""
        if isinstance(event, RawContentBlockStartEvent):
            state = context.get_request_state(self, _StreamState, _StreamState)
            state.max_index = max(state.max_index, event.index)
            return [event]
        if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
            new_delta = event.delta.model_copy(update={"text": self.modify_text(event.delta.text)})
            return [event.model_copy(update={"delta": new_delta})]
        return [event]

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        """Emit extra_text as a new content block after the stream ends, if configured."""
        suffix = self.extra_text()
        if suffix is None:
            return []
        state = context.get_request_state(self, _StreamState, _StreamState)
        new_index = state.max_index + 1
        return [
            cast(
                MessageStreamEvent,
                RawContentBlockStartEvent(
                    type="content_block_start",
                    index=new_index,
                    content_block=TextBlock(type="text", text=""),
                ),
            ),
            cast(
                MessageStreamEvent,
                RawContentBlockDeltaEvent(
                    type="content_block_delta",
                    index=new_index,
                    delta=TextDelta(type="text_delta", text=suffix),
                ),
            ),
            cast(
                MessageStreamEvent,
                RawContentBlockStopEvent(
                    type="content_block_stop",
                    index=new_index,
                ),
            ),
        ]


__all__ = ["TextModifierPolicy"]
