"""Default hook implementations for Anthropic policies.

Provides passthrough defaults for all four lifecycle hooks so that
subclasses only need to override the hooks they care about.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent

from luthien_proxy.policy_core.anthropic_execution_interface import AnthropicPolicyEmission

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext


class AnthropicHookPolicy:
    """Mixin providing passthrough defaults for all Anthropic lifecycle hooks.

    Subclasses override only the hooks they need. The executor calls these
    hooks around backend I/O — policies never drive execution themselves.

    Hooks:
        on_anthropic_request: Called before sending request. Default: passthrough.
        on_anthropic_response: Called on non-streaming response. Default: passthrough.
        on_anthropic_stream_event: Called per stream event. Default: passthrough.
        on_anthropic_stream_complete: Called after stream ends. Default: no extra events.
    """

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
        """Emit additional events after the upstream stream ends. Default: none."""
        return []


__all__ = ["AnthropicHookPolicy"]
