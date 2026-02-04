# ABOUTME: Abstract base class defining the Anthropic policy interface

"""Abstract base class defining the Anthropic policy interface.

This module defines AnthropicPolicyInterface with hooks for:
- Non-streaming request and response processing
- Streaming event processing with filtering and transformation

Policies implementing this interface work with native Anthropic types,
avoiding format conversion overhead and preserving Anthropic-specific features.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

# Use the SDK's MessageStreamEvent which includes all streaming event types
# (TextEvent, CitationEvent, ThinkingEvent, Raw* events, etc.)
AnthropicStreamEvent = MessageStreamEvent


class AnthropicPolicyInterface(ABC):
    """Abstract base class for policies that work with native Anthropic types.

    This interface defines hooks for processing Anthropic API requests and responses
    without converting to/from OpenAI format. This preserves Anthropic-specific
    features like extended thinking, tool use patterns, and prompt caching.

    For non-streaming:
    - on_anthropic_request: Transform request before sending to Anthropic
    - on_anthropic_response: Transform response before returning to client

    For streaming:
    - on_anthropic_stream_event: Process each streaming event, can filter or transform
    """

    @abstractmethod
    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Process request before sending to Anthropic API.

        Args:
            request: The Anthropic Messages API request
            context: Policy context with scratchpad, emitter, etc.

        Returns:
            Potentially modified request to send to Anthropic
        """
        ...

    @abstractmethod
    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Process non-streaming response after receiving from Anthropic.

        Args:
            response: The Anthropic Messages API response
            context: Policy context with scratchpad, emitter, etc.

        Returns:
            Potentially modified response to return to client
        """
        ...

    @abstractmethod
    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: "PolicyContext"
    ) -> AnthropicStreamEvent | None:
        """Process a streaming event from Anthropic.

        This hook is called for each SSE event in a streaming response.
        Policies can:
        - Return the event unchanged (passthrough)
        - Return a modified event (transformation)
        - Return None to filter out the event

        Args:
            event: The Anthropic streaming event
            context: Policy context with scratchpad, emitter, etc.

        Returns:
            The event to emit (possibly modified), or None to filter it out
        """
        ...


__all__ = [
    "AnthropicPolicyInterface",
    "AnthropicStreamEvent",
]
