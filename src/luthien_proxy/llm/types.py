"""OpenAI-compatible message and request types.

These types define the structure of messages and requests in the OpenAI chat completion
API format. They are used for type-safe message handling throughout the proxy.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

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
# Tool Definition Types (OpenAI API spec)
# =============================================================================


class FunctionParameters(TypedDict, total=False):
    """JSON Schema for function parameters."""

    type: str  # Usually "object"
    properties: dict[str, Any]
    required: list[str]


class FunctionDefinition(TypedDict, total=False):
    """Function definition within a tool."""

    name: str  # Required
    description: str  # Optional but recommended
    parameters: FunctionParameters  # Optional


class ToolDefinition(TypedDict):
    """Tool definition for function calling."""

    type: Literal["function"]
    function: FunctionDefinition


# =============================================================================
# Request Types (OpenAI API spec)
# =============================================================================


class OpenAIRequestDict(TypedDict, total=False):
    """OpenAI chat completion request as a TypedDict.

    This represents the raw dict format used in API calls.
    For Pydantic validation, use luthien_proxy.messages.Request instead.
    """

    model: str  # Required
    messages: list[Message]  # Required
    max_tokens: int  # Optional
    temperature: float  # Optional
    top_p: float  # Optional
    stream: bool  # Optional
    tools: list[ToolDefinition]  # Optional
    tool_choice: str | dict[str, Any]  # Optional


# =============================================================================
# Anthropic Types (for format conversion)
# =============================================================================


class AnthropicImageSource(TypedDict, total=False):
    """Anthropic image source."""

    type: Literal["base64", "url"]
    media_type: str  # e.g., "image/png"
    data: str  # base64 data (when type="base64")
    url: str  # URL (when type="url")


class AnthropicTextBlock(TypedDict):
    """Anthropic text content block."""

    type: Literal["text"]
    text: str


class AnthropicImageBlock(TypedDict):
    """Anthropic image content block."""

    type: Literal["image"]
    source: AnthropicImageSource


class AnthropicToolUseBlock(TypedDict, total=False):
    """Anthropic tool use block (assistant requesting tool call)."""

    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, Any]


class AnthropicToolResultBlock(TypedDict, total=False):
    """Anthropic tool result block (user providing tool result)."""

    type: Literal["tool_result"]
    tool_use_id: str
    content: str | list[AnthropicTextBlock]
    is_error: bool


AnthropicContentBlock = AnthropicTextBlock | AnthropicImageBlock | AnthropicToolUseBlock | AnthropicToolResultBlock


class AnthropicMessage(TypedDict, total=False):
    """Anthropic message format."""

    role: Literal["user", "assistant"]
    content: str | list[AnthropicContentBlock]


class AnthropicToolDefinition(TypedDict, total=False):
    """Anthropic tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]


class AnthropicRequestDict(TypedDict, total=False):
    """Anthropic Messages API request format."""

    model: str  # Required
    messages: list[AnthropicMessage]  # Required
    max_tokens: int  # Required for Anthropic
    system: str | list[AnthropicTextBlock]  # Optional
    temperature: float  # Optional
    top_p: float  # Optional
    stream: bool  # Optional
    tools: list[AnthropicToolDefinition]  # Optional


# =============================================================================
# Response Types (Anthropic format)
# =============================================================================


class AnthropicUsage(TypedDict):
    """Anthropic usage statistics."""

    input_tokens: int
    output_tokens: int


class AnthropicResponseTextBlock(TypedDict):
    """Anthropic response text block."""

    type: Literal["text"]
    text: str


class AnthropicResponseToolUseBlock(TypedDict):
    """Anthropic response tool use block."""

    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, Any]


AnthropicResponseContentBlock = AnthropicResponseTextBlock | AnthropicResponseToolUseBlock


class AnthropicResponseDict(TypedDict, total=False):
    """Anthropic Messages API response format."""

    id: str
    type: Literal["message"]
    role: Literal["assistant"]
    content: list[AnthropicResponseContentBlock]
    model: str
    usage: AnthropicUsage
    stop_reason: str  # "end_turn", "tool_use", "max_tokens", etc.


__all__ = [
    # Content parts
    "ImageUrl",
    "TextContentPart",
    "ImageContentPart",
    "ContentPart",
    "MessageContent",
    # Messages
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolMessage",
    "FunctionCall",
    "ToolCall",
    "Message",
    # Tools
    "FunctionParameters",
    "FunctionDefinition",
    "ToolDefinition",
    # OpenAI request
    "OpenAIRequestDict",
    # Anthropic types
    "AnthropicImageSource",
    "AnthropicTextBlock",
    "AnthropicImageBlock",
    "AnthropicToolUseBlock",
    "AnthropicToolResultBlock",
    "AnthropicContentBlock",
    "AnthropicMessage",
    "AnthropicToolDefinition",
    "AnthropicRequestDict",
    # Anthropic response
    "AnthropicUsage",
    "AnthropicResponseTextBlock",
    "AnthropicResponseToolUseBlock",
    "AnthropicResponseContentBlock",
    "AnthropicResponseDict",
]
