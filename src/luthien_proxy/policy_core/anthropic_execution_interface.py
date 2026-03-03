"""Execution-oriented policy interface for Anthropic request handling.

This interface lets policies own request execution end-to-end:
- they may call backend LLMs zero or more times
- they may emit client-facing streaming events independently of backend chunking
- they may emit a final non-streaming response without any backend call
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
    """I/O surface exposed to Anthropic execution policies.

    Session metadata is available via PolicyContext (not this protocol).
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
    """Execution-oriented Anthropic policy contract.

    Policies implementing this interface drive execution themselves and emit
    outbound artifacts (stream events or a final response) for the client.
    """

    def run_anthropic(
        self,
        io: AnthropicPolicyIOProtocol,
        context: "PolicyContext",
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Run policy execution and emit outbound artifacts."""
        ...


__all__ = [
    "AnthropicExecutionInterface",
    "AnthropicPolicyEmission",
    "AnthropicPolicyIOProtocol",
]
