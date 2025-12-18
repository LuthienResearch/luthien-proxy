"""Anthropic-specific message types.

These types define Anthropic API structures used for format conversion
between OpenAI and Anthropic formats.
"""

from __future__ import annotations

from typing import Literal, Required, TypedDict

# =============================================================================
# Anthropic Content Types (for format conversion)
# =============================================================================


class AnthropicImageSourceBase64(TypedDict):
    """Anthropic image source block for base64-encoded images."""

    type: Literal["base64"]
    media_type: str  # e.g., "image/png", "image/jpeg"
    data: str  # base64-encoded data


class AnthropicImageSourceUrl(TypedDict):
    """Anthropic image source block for URL images."""

    type: Literal["url"]
    url: str


# Union of image source types
AnthropicImageSource = AnthropicImageSourceBase64 | AnthropicImageSourceUrl


class AnthropicImageBlock(TypedDict):
    """Anthropic image content block."""

    type: Literal["image"]
    source: AnthropicImageSource


class AnthropicTextBlock(TypedDict):
    """Anthropic text content block."""

    type: Literal["text"]
    text: str


class AnthropicToolUseBlock(TypedDict):
    """Anthropic tool use content block."""

    type: Literal["tool_use"]
    id: str
    name: str
    input: dict


class AnthropicToolResultBlock(TypedDict, total=False):
    """Anthropic tool result content block."""

    type: Required[Literal["tool_result"]]
    tool_use_id: Required[str]
    content: str | list[AnthropicTextBlock | AnthropicImageBlock]
    is_error: bool  # Optional


# Union of all Anthropic content block types
AnthropicContentBlock = AnthropicTextBlock | AnthropicImageBlock | AnthropicToolUseBlock | AnthropicToolResultBlock


class AnthropicMessage(TypedDict, total=False):
    """Anthropic message format."""

    role: Required[Literal["user", "assistant"]]
    content: Required[str | list[AnthropicContentBlock]]


class AnthropicUsage(TypedDict):
    """Anthropic usage information."""

    input_tokens: int
    output_tokens: int


class AnthropicResponse(TypedDict, total=False):
    """Anthropic API response format."""

    id: Required[str]
    type: Required[Literal["message"]]
    role: Required[Literal["assistant"]]
    content: Required[list[AnthropicContentBlock]]
    model: Required[str]
    usage: Required[AnthropicUsage]
    stop_reason: str | None


__all__ = [
    # Image types
    "AnthropicImageSourceBase64",
    "AnthropicImageSourceUrl",
    "AnthropicImageSource",
    "AnthropicImageBlock",
    # Content blocks
    "AnthropicTextBlock",
    "AnthropicToolUseBlock",
    "AnthropicToolResultBlock",
    "AnthropicContentBlock",
    # Messages and responses
    "AnthropicMessage",
    "AnthropicUsage",
    "AnthropicResponse",
]
