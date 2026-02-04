# ABOUTME: Protocol defining the Anthropic-native policy interface for request/response processing

"""Protocol defining the Anthropic-native policy interface.

This module defines AnthropicPolicyProtocol with hooks for:
- Non-streaming request and response processing
- Streaming event processing with filtering and transformation

Policies implementing this protocol work with native Anthropic types,
avoiding format conversion overhead and preserving Anthropic-specific features.

Streaming event types are imported from luthien_proxy.llm.types.anthropic and
re-exported here with shorter aliases for backward compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

# Import canonical streaming event types with backward-compatible short aliases
# ruff: noqa: E501
from luthien_proxy.llm.types.anthropic import AnthropicContentBlockDeltaEvent as ContentBlockDelta
from luthien_proxy.llm.types.anthropic import AnthropicContentBlockStartEvent as ContentBlockStart
from luthien_proxy.llm.types.anthropic import AnthropicContentBlockStopEvent as ContentBlockStop
from luthien_proxy.llm.types.anthropic import AnthropicErrorEvent as ErrorEvent
from luthien_proxy.llm.types.anthropic import AnthropicInputJSONDelta as InputJsonDelta
from luthien_proxy.llm.types.anthropic import AnthropicMessageDelta as MessageDeltaDelta
from luthien_proxy.llm.types.anthropic import AnthropicMessageDeltaEvent as MessageDelta
from luthien_proxy.llm.types.anthropic import AnthropicMessageDeltaUsage as MessageDeltaUsage
from luthien_proxy.llm.types.anthropic import AnthropicMessageStartEvent as MessageStart
from luthien_proxy.llm.types.anthropic import AnthropicMessageStopEvent as MessageStop
from luthien_proxy.llm.types.anthropic import AnthropicPingEvent as Ping
from luthien_proxy.llm.types.anthropic import AnthropicSignatureDelta as SignatureDelta
from luthien_proxy.llm.types.anthropic import AnthropicStreamingContentBlock as ContentBlockStartBlock
from luthien_proxy.llm.types.anthropic import AnthropicStreamingDelta as ContentBlockDeltaType
from luthien_proxy.llm.types.anthropic import AnthropicStreamingEvent as AnthropicStreamEvent
from luthien_proxy.llm.types.anthropic import AnthropicStreamingMessage as MessageStartMessage
from luthien_proxy.llm.types.anthropic import AnthropicStreamingThinkingBlock as ThinkingBlockStart
from luthien_proxy.llm.types.anthropic import AnthropicStreamingToolUseBlock as ToolUseBlockStart
from luthien_proxy.llm.types.anthropic import AnthropicTextBlock as TextBlockStart
from luthien_proxy.llm.types.anthropic import AnthropicTextDelta as TextDelta
from luthien_proxy.llm.types.anthropic import AnthropicThinkingDelta as ThinkingDelta
from luthien_proxy.llm.types.anthropic import AnthropicUsage as MessageStartUsage

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext


# =============================================================================
# Anthropic Policy Protocol
# =============================================================================


@runtime_checkable
class AnthropicPolicyProtocol(Protocol):
    """Protocol for policies that work with native Anthropic types.

    This protocol defines hooks for processing Anthropic API requests and responses
    without converting to/from OpenAI format. This preserves Anthropic-specific
    features like extended thinking, tool use patterns, and prompt caching.

    For non-streaming:
    - on_request: Transform request before sending to Anthropic
    - on_response: Transform response before returning to client

    For streaming:
    - on_stream_event: Process each streaming event, can filter or transform
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy (e.g., 'NoOp', 'AllCaps')."""
        ...

    async def on_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Process request before sending to Anthropic API.

        Args:
            request: The Anthropic Messages API request
            context: Policy context with scratchpad, emitter, etc.

        Returns:
            Potentially modified request to send to Anthropic
        """
        ...

    async def on_response(self, response: "AnthropicResponse", context: "PolicyContext") -> "AnthropicResponse":
        """Process non-streaming response after receiving from Anthropic.

        Args:
            response: The Anthropic Messages API response
            context: Policy context with scratchpad, emitter, etc.

        Returns:
            Potentially modified response to return to client
        """
        ...

    async def on_stream_event(
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
    # Protocol
    "AnthropicPolicyProtocol",
    # Stream events (backward-compatible short names)
    "AnthropicStreamEvent",
    "MessageStart",
    "MessageStartMessage",
    "MessageStartUsage",
    "ContentBlockStart",
    "ContentBlockStartBlock",
    "TextBlockStart",
    "ThinkingBlockStart",
    "ToolUseBlockStart",
    "ContentBlockDelta",
    "ContentBlockDeltaType",
    "TextDelta",
    "ThinkingDelta",
    "SignatureDelta",
    "InputJsonDelta",
    "ContentBlockStop",
    "MessageDelta",
    "MessageDeltaDelta",
    "MessageDeltaUsage",
    "MessageStop",
    "Ping",
    "ErrorEvent",
]
