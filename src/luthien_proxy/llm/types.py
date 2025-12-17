"""OpenAI-compatible message types.

These types define the structure of messages in the OpenAI chat completion API format.
They are used for type-safe message handling throughout the proxy.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# =============================================================================
# Content Part Types (OpenAI API spec)
# =============================================================================


class ImageUrl(TypedDict, total=False):
    """Image URL object per OpenAI spec."""

    url: str  # Required: URL or base64 data URI
    detail: Literal["auto", "low", "high"]  # Optional


class TextContentPart(TypedDict):
    """Text content block."""

    type: Literal["text"]
    text: str


class ImageContentPart(TypedDict):
    """Image content block."""

    type: Literal["image_url"]
    image_url: ImageUrl


# Union of all content part types
ContentPart = TextContentPart | ImageContentPart

# Content can be a simple string or a list of content parts (for multimodal)
MessageContent = str | list[ContentPart]


# =============================================================================
# Message Types (OpenAI API spec)
# =============================================================================


class SystemMessage(TypedDict, total=False):
    """System message."""

    role: Literal["system"]  # Required
    content: str | list[TextContentPart]  # Required (system only supports text)
    name: str  # Optional


class UserMessage(TypedDict, total=False):
    """User message - supports multimodal content (text + images)."""

    role: Literal["user"]  # Required
    content: MessageContent  # Required
    name: str  # Optional


class FunctionCall(TypedDict):
    """Function call in assistant message."""

    name: str
    arguments: str


class ToolCall(TypedDict):
    """Tool call in assistant message."""

    id: str
    type: Literal["function"]
    function: FunctionCall


class AssistantMessage(TypedDict, total=False):
    """Assistant message."""

    role: Literal["assistant"]  # Required
    content: str | None  # Optional (can be None when tool_calls present)
    name: str  # Optional
    tool_calls: list[ToolCall]  # Optional
    function_call: FunctionCall  # Optional (deprecated)


class ToolMessage(TypedDict, total=False):
    """Tool result message."""

    role: Literal["tool"]  # Required
    content: str | list[TextContentPart]  # Required
    tool_call_id: str  # Required


# Union of all message types
Message = SystemMessage | UserMessage | AssistantMessage | ToolMessage


# =============================================================================
# Anthropic Content Types (for format conversion)
# =============================================================================


class AnthropicImageSource(TypedDict, total=False):
    """Anthropic image source block."""

    type: Literal["base64", "url"]
    media_type: str  # e.g., "image/png", "image/jpeg"
    data: str  # base64-encoded data (when type="base64")
    url: str  # URL (when type="url")


class AnthropicImageBlock(TypedDict):
    """Anthropic image content block."""

    type: Literal["image"]
    source: AnthropicImageSource


__all__ = [
    # OpenAI content parts
    "ImageUrl",
    "TextContentPart",
    "ImageContentPart",
    "ContentPart",
    "MessageContent",
    # OpenAI messages
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolMessage",
    "FunctionCall",
    "ToolCall",
    "Message",
    # Anthropic content types (for format conversion)
    "AnthropicImageSource",
    "AnthropicImageBlock",
]
