"""Hook-based policy interface for Anthropic request handling.

Policies implement four lifecycle hooks that the executor calls in sequence:
- on_anthropic_request: transform request before sending to backend
- on_anthropic_response: transform non-streaming response
- on_anthropic_stream_event: transform/filter individual stream events
- on_anthropic_stream_complete: emit additional events after stream ends

The executor owns the backend call and stream iteration — policies only
see hooks, never the IO layer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from anthropic.lib.streaming import MessageStreamEvent

from luthien_proxy.llm.types.anthropic import AnthropicResponse

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicRequest
    from luthien_proxy.policy_core.policy_context import PolicyContext


type AnthropicPolicyEmission = AnthropicResponse | MessageStreamEvent


class AnthropicPolicyIOProtocol(Protocol):
    """Request-scoped I/O surface used by the executor (not by policies).

    One instance is created per request; holds the mutable request payload,
    accumulated backend response, and methods for calling the backend.
    """

    @property
    def request(self) -> "AnthropicRequest":
        """Current Anthropic request payload."""
        ...

    def set_request(self, request: "AnthropicRequest") -> None:
        """Replace the current request payload."""
        ...

    @property
    def first_backend_response(self) -> "AnthropicResponse | None":
        """First backend response observed during this request execution."""
        ...

    async def complete(self, request: "AnthropicRequest | None" = None) -> "AnthropicResponse":
        """Execute a non-streaming backend call."""
        ...

    def stream(self, request: "AnthropicRequest | None" = None) -> AsyncIterator[MessageStreamEvent]:
        """Execute a streaming backend call."""
        ...


@runtime_checkable
class AnthropicExecutionInterface(Protocol):
    """Hook-based Anthropic policy contract.

    Policies implement these hooks; the executor calls them around backend
    I/O. Policies never see the IO layer directly.
    """

    async def on_anthropic_request(
        self,
        request: "AnthropicRequest",
        context: "PolicyContext",
    ) -> "AnthropicRequest":
        """Transform request before sending to backend. Default: passthrough."""
        ...

    async def on_anthropic_response(
        self,
        response: "AnthropicResponse",
        context: "PolicyContext",
    ) -> "AnthropicResponse":
        """Transform non-streaming response. Default: passthrough."""
        ...

    async def on_anthropic_stream_event(
        self,
        event: MessageStreamEvent,
        context: "PolicyContext",
    ) -> list[MessageStreamEvent]:
        """Transform/filter a single stream event. Default: passthrough."""
        ...

    async def on_anthropic_stream_complete(
        self,
        context: "PolicyContext",
    ) -> list[AnthropicPolicyEmission]:
        """Emit additional events after upstream stream ends. Default: none."""
        ...


__all__ = [
    "AnthropicExecutionInterface",
    "AnthropicPolicyEmission",
    "AnthropicPolicyIOProtocol",
]
