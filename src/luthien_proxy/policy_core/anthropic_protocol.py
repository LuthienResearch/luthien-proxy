# ABOUTME: Protocol defining the Anthropic-native policy interface for request/response processing

"""Protocol defining the Anthropic-native policy interface.

This module defines AnthropicPolicyProtocol with hooks for:
- Non-streaming request and response processing
- Streaming event processing with filtering and transformation

Policies implementing this protocol work with native Anthropic types,
avoiding format conversion overhead and preserving Anthropic-specific features.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, Required, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext


# =============================================================================
# Streaming Event Types (Anthropic Messages API streaming format)
# =============================================================================


class MessageStartMessage(TypedDict, total=False):
    """The message object in a message_start event."""

    id: Required[str]
    type: Required[Literal["message"]]
    role: Required[Literal["assistant"]]
    content: Required[list["AnthropicContentBlock"]]
    model: Required[str]
    stop_reason: Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"] | None
    stop_sequence: str | None
    usage: Required["MessageStartUsage"]


class MessageStartUsage(TypedDict):
    """Usage info in message_start event."""

    input_tokens: int
    output_tokens: int


class MessageStart(TypedDict):
    """Event sent at the start of a message stream."""

    type: Literal["message_start"]
    message: MessageStartMessage


# Content block types for streaming
class TextBlockStart(TypedDict):
    """Text content block at start (empty text)."""

    type: Literal["text"]
    text: str


class ThinkingBlockStart(TypedDict):
    """Thinking content block at start."""

    type: Literal["thinking"]
    thinking: str


class ToolUseBlockStart(TypedDict, total=False):
    """Tool use content block at start."""

    type: Required[Literal["tool_use"]]
    id: Required[str]
    name: Required[str]
    input: Required[dict]


ContentBlockStartBlock = TextBlockStart | ThinkingBlockStart | ToolUseBlockStart


class ContentBlockStart(TypedDict):
    """Event sent when a content block starts."""

    type: Literal["content_block_start"]
    index: int
    content_block: ContentBlockStartBlock


# Delta types for streaming
class TextDelta(TypedDict):
    """Text content delta."""

    type: Literal["text_delta"]
    text: str


class ThinkingDelta(TypedDict):
    """Thinking content delta."""

    type: Literal["thinking_delta"]
    thinking: str


class SignatureDelta(TypedDict):
    """Signature delta for thinking blocks."""

    type: Literal["signature_delta"]
    signature: str


class InputJsonDelta(TypedDict):
    """Tool input JSON delta."""

    type: Literal["input_json_delta"]
    partial_json: str


ContentBlockDeltaType = TextDelta | ThinkingDelta | SignatureDelta | InputJsonDelta


class ContentBlockDelta(TypedDict):
    """Event sent when content is added to a block."""

    type: Literal["content_block_delta"]
    index: int
    delta: ContentBlockDeltaType


class ContentBlockStop(TypedDict):
    """Event sent when a content block completes."""

    type: Literal["content_block_stop"]
    index: int


class MessageDeltaDelta(TypedDict, total=False):
    """Delta info in message_delta event."""

    stop_reason: Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"] | None
    stop_sequence: str | None


class MessageDeltaUsage(TypedDict):
    """Usage info in message_delta event."""

    output_tokens: int


class MessageDelta(TypedDict):
    """Event sent when message-level data changes (e.g., stop_reason)."""

    type: Literal["message_delta"]
    delta: MessageDeltaDelta
    usage: MessageDeltaUsage


class MessageStop(TypedDict):
    """Event sent at the end of a message stream."""

    type: Literal["message_stop"]


class Ping(TypedDict):
    """Keepalive ping event."""

    type: Literal["ping"]


class ErrorEvent(TypedDict):
    """Error event during streaming."""

    type: Literal["error"]
    error: dict


# Union of all stream event types
AnthropicStreamEvent = (
    MessageStart
    | ContentBlockStart
    | ContentBlockDelta
    | ContentBlockStop
    | MessageDelta
    | MessageStop
    | Ping
    | ErrorEvent
)


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
    # Stream events
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
