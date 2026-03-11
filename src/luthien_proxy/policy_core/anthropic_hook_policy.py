"""Hook-based base class for Anthropic policy execution.

Provides a default run_anthropic that delegates to four overridable hooks:
- on_anthropic_request: transform request before sending
- on_anthropic_response: transform non-streaming response
- on_anthropic_stream_event: transform/filter individual stream events
- on_anthropic_stream_complete: emit additional events after stream ends

This eliminates the copy-pasted run_anthropic boilerplate that was
duplicated across 7+ policies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent

from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext


class AnthropicHookPolicy(AnthropicExecutionInterface):
    """Hook-based Anthropic execution with overridable lifecycle methods.

    Provides a standard run_anthropic that handles the stream-vs-complete
    branching and delegates to hooks. Override only the hooks you need.

    Hooks:
        on_anthropic_request: Called before sending request. Default: passthrough.
        on_anthropic_response: Called on non-streaming response. Default: passthrough.
        on_anthropic_stream_event: Called per stream event. Default: passthrough.
        on_anthropic_stream_complete: Called after stream ends. Default: no extra events.
            This is the hook that was missing from the old pattern — it lets policies
            emit events after the upstream stream finishes without rewriting run_anthropic.
    """

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: PolicyContext
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Standard Anthropic execution: request hook -> stream/complete -> response hooks."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            final_request = await self.on_anthropic_request(io.request, context)
            io.set_request(final_request)

            if final_request.get("stream", False):
                async for event in io.stream(final_request):
                    emitted_events = await self.on_anthropic_stream_event(event, context)
                    for emitted_event in emitted_events:
                        yield emitted_event

                post_stream_events = await self.on_anthropic_stream_complete(context)
                for event in post_stream_events:
                    yield event
                return

            response = await io.complete(final_request)
            yield await self.on_anthropic_response(response, context)

        return _run()

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        """Transform request before sending. Default: passthrough."""
        return request

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        """Transform non-streaming response. Default: passthrough."""
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        """Transform/filter a stream event. Default: passthrough."""
        return [event]

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        """Emit additional events after the upstream stream ends. Default: none.

        This is the hook for appending content after streaming completes
        (e.g., indicator suffixes, safety notices). Return a list of events
        to emit, or empty list for no additions.
        """
        return []


__all__ = ["AnthropicHookPolicy"]
