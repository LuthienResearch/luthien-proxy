"""Anthropic message types.

These types define the structure of messages in the Anthropic Messages API format.
They are used for format conversion between OpenAI and Anthropic APIs.
"""

from __future__ import annotations

from typing import Literal, Required, TypedDict

# JSON-compatible types for API serialization
# These represent valid JSON values that can be sent to/received from the Anthropic API
type JSONPrimitive = str | int | float | bool | None
type JSONValue = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]
type JSONObject = dict[str, JSONValue]

# =============================================================================
# Cache Control Types (Anthropic API spec)
# =============================================================================


class AnthropicCacheControl(TypedDict):
    """Anthropic cache control for prompt caching."""

    type: Literal["ephemeral"]


# =============================================================================
# Content Block Types (Anthropic API spec)
# =============================================================================


class AnthropicTextBlock(TypedDict):
    """Anthropic text content block."""

    type: Literal["text"]
    text: str


class AnthropicImageSourceBase64(TypedDict):
    """Anthropic base64 image source."""

    type: Literal["base64"]
    media_type: str  # e.g., "image/png", "image/jpeg"
    data: str  # base64-encoded data


class AnthropicImageSourceUrl(TypedDict):
    """Anthropic URL image source."""

    type: Literal["url"]
    url: str


# Union of image source types
AnthropicImageSource = AnthropicImageSourceBase64 | AnthropicImageSourceUrl


class AnthropicImageBlock(TypedDict):
    """Anthropic image content block."""

    type: Literal["image"]
    source: AnthropicImageSource


class AnthropicToolUseBlock(TypedDict):
    """Anthropic tool use content block (assistant requesting tool call)."""

    type: Literal["tool_use"]
    id: str
    name: str
    input: JSONObject  # Tool input parameters as JSON object


class AnthropicToolResultBlock(TypedDict, total=False):
    """Anthropic tool result content block (user providing tool output)."""

    type: Required[Literal["tool_result"]]
    tool_use_id: Required[str]
    content: str | list[AnthropicTextBlock | AnthropicImageBlock]
    is_error: bool


# Union of all content block types
AnthropicContentBlock = AnthropicTextBlock | AnthropicImageBlock | AnthropicToolUseBlock | AnthropicToolResultBlock


# =============================================================================
# Message Types (Anthropic API spec)
# =============================================================================


class AnthropicUserMessage(TypedDict):
    """Anthropic user message."""

    role: Literal["user"]
    content: str | list[AnthropicContentBlock]


class AnthropicAssistantMessage(TypedDict):
    """Anthropic assistant message."""

    role: Literal["assistant"]
    content: str | list[AnthropicContentBlock]


# Union of message types
AnthropicMessage = AnthropicUserMessage | AnthropicAssistantMessage


# =============================================================================
# System Content Types (Anthropic API spec)
# =============================================================================


class AnthropicSystemBlock(TypedDict, total=False):
    """Anthropic system content block with optional cache control."""

    type: Required[Literal["text"]]
    text: Required[str]
    cache_control: AnthropicCacheControl


# System can be a string or list of system blocks
AnthropicSystemContent = str | list[AnthropicSystemBlock]


# =============================================================================
# Tool Definition Types (Anthropic API spec)
# =============================================================================

# JSON Schema type for tool input schemas
# The Anthropic API expects a JSON Schema object with type: "object"
JSONSchemaObject = JSONObject


class AnthropicTool(TypedDict, total=False):
    """Anthropic tool definition."""

    name: Required[str]
    description: str
    input_schema: Required[JSONSchemaObject]


# =============================================================================
# Response Types (Anthropic API spec)
# =============================================================================


class AnthropicUsage(TypedDict):
    """Anthropic usage statistics."""

    input_tokens: int
    output_tokens: int


class AnthropicResponse(TypedDict, total=False):
    """Anthropic Messages API response."""

    id: Required[str]
    type: Required[Literal["message"]]
    role: Required[Literal["assistant"]]
    content: Required[list[AnthropicContentBlock]]
    model: Required[str]
    stop_reason: Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"] | None
    stop_sequence: str | None
    usage: Required[AnthropicUsage]


__all__ = [
    # JSON types
    "JSONPrimitive",
    "JSONValue",
    "JSONObject",
    # Cache control
    "AnthropicCacheControl",
    # Content blocks
    "AnthropicTextBlock",
    "AnthropicImageSourceBase64",
    "AnthropicImageSourceUrl",
    "AnthropicImageSource",
    "AnthropicImageBlock",
    "AnthropicToolUseBlock",
    "AnthropicToolResultBlock",
    "AnthropicContentBlock",
    # Messages
    "AnthropicUserMessage",
    "AnthropicAssistantMessage",
    "AnthropicMessage",
    # System content
    "AnthropicSystemBlock",
    "AnthropicSystemContent",
    # Tools
    "JSONSchemaObject",
    "AnthropicTool",
    # Response types
    "AnthropicUsage",
    "AnthropicResponse",
]
