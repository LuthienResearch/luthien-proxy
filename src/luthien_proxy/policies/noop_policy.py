"""No-op policy that performs no modifications."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent
from litellm.types.utils import ModelResponse

from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.policy_context import PolicyContext

if TYPE_CHECKING:
    from luthien_proxy.llm.types import Request
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)


class NoOpPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """No-op policy that passes through all data unchanged.

    Implements OpenAIPolicyInterface and AnthropicExecutionInterface.
    Anthropic helper methods return inputs unchanged.
    """

    @property
    def short_policy_name(self) -> str:
        """Return 'NoOp'."""
        return "NoOp"

    # -- OpenAI interface hooks ------------------------------------------------

    async def on_openai_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass through unchanged."""
        return request

    async def on_openai_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Pass through unchanged."""
        return response

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Pass through chunk unchanged."""
        ctx.push_chunk(ctx.last_chunk_received)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    # -- Anthropic execution interface ------------------------------------------

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: PolicyContext
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Pass through Anthropic request/response/streaming unchanged."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            final_request = await self.on_anthropic_request(io.request, context)
            io.set_request(final_request)

            if final_request.get("stream", False):
                async for event in io.stream(final_request):
                    emitted_events = await self.on_anthropic_stream_event(event, context)
                    for emitted_event in emitted_events:
                        yield emitted_event
                return

            response = await io.complete(final_request)
            yield await self.on_anthropic_response(response, context)

        return _run()

    # -- Anthropic helpers -----------------------------------------------------

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        """Pass through unchanged."""
        return request

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        """Pass through unchanged."""
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        """Pass through unchanged."""
        return [event]


__all__ = ["NoOpPolicy"]
