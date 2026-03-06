"""Base class for policies that modify text content across all API formats.

Subclasses override one or two methods:
- modify_text(text) -> text: transform text content in-place
- extra_text() -> str | None: append additional text after all content

The base class handles all format-specific plumbing across 4 code paths:
OpenAI non-streaming, OpenAI streaming, Anthropic non-streaming, Anthropic streaming.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextBlock,
    TextDelta,
)
from litellm.types.utils import Choices, StreamingChoices

from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.chunk_builders import create_text_chunk

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.llm.types.anthropic import AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext


class TextModifierPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """Base class for policies that modify text content.

    Override modify_text() to transform text in-place across all code paths.
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

    # -- OpenAI non-streaming --------------------------------------------------

    async def on_openai_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass through request unchanged."""
        return request

    async def on_openai_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Apply modify_text and extra_text to OpenAI non-streaming responses."""
        if not response.choices:
            return response

        for choice in response.choices:
            if isinstance(choice, Choices) and isinstance(choice.message.content, str):
                choice.message.content = self.modify_text(choice.message.content)
                suffix = self.extra_text()
                if suffix is not None:
                    choice.message.content += suffix

        return response

    # -- OpenAI streaming ------------------------------------------------------

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Apply modify_text to streaming text chunks and forward them."""
        chunk = ctx.last_chunk_received
        if chunk.choices:
            choice = cast(StreamingChoices, chunk.choices[0])
            if hasattr(choice, "delta") and choice.delta and choice.delta.content is not None:
                choice.delta.content = self.modify_text(choice.delta.content)
        ctx.push_chunk(chunk)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op — text transformation handled in on_chunk_received."""

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op — content completion needs no special handling."""

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op — tool calls pass through unchanged."""

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op — tool calls pass through unchanged."""

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """No-op — finish reason passes through unchanged."""

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """Emit extra_text as a final chunk if configured."""
        suffix = self.extra_text()
        if suffix is None:
            return
        model = ctx.request.model if ctx.request else "luthien-policy"
        ctx.push_chunk(create_text_chunk(suffix, model=model))

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op — no per-request cleanup needed."""

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


__all__ = ["TextModifierPolicy"]
