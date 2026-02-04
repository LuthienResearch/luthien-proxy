# ABOUTME: No-op policy implementing AnthropicPolicyProtocol as pure passthrough
"""No-op policy for Anthropic-native requests.

This policy passes through all requests, responses, and stream events unchanged.
It validates that the AnthropicPolicyProtocol infrastructure works correctly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from luthien_proxy.policy_core.anthropic_protocol import (
    AnthropicStreamEvent,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext


class AnthropicNoOpPolicy:
    """No-op policy that passes through all Anthropic data unchanged.

    Implements AnthropicPolicyProtocol as the simplest possible implementation:
    - on_request returns the request unchanged
    - on_response returns the response unchanged
    - on_stream_event returns the event unchanged (never filters)
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "AnthropicNoOp"

    async def on_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Pass through request unchanged."""
        return request

    async def on_response(self, response: "AnthropicResponse", context: "PolicyContext") -> "AnthropicResponse":
        """Pass through response unchanged."""
        return response

    async def on_stream_event(
        self, event: AnthropicStreamEvent, context: "PolicyContext"
    ) -> AnthropicStreamEvent | None:
        """Pass through stream event unchanged."""
        return event


__all__ = ["AnthropicNoOpPolicy"]
