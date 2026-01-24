"""OpenAI-compatible message types.

These types define the structure of messages in the OpenAI chat completion API format.
They are used for type-safe message handling throughout the proxy.
"""

from __future__ import annotations

from typing import Any, Literal, Required, TypedDict

from pydantic import BaseModel, Field

# =============================================================================
# Content Part Types (OpenAI API spec)
# =============================================================================


class ImageUrl(TypedDict, total=False):
    """Image URL object per OpenAI spec."""

    url: Required[str]  # URL or base64 data URI
    detail: Literal["auto", "low", "high"]


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

    role: Required[Literal["system"]]
    content: Required[str | list[TextContentPart]]  # system only supports text
    name: str


class UserMessage(TypedDict, total=False):
    """User message - supports multimodal content (text + images)."""

    role: Required[Literal["user"]]
    content: Required[MessageContent]
    name: str


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

    role: Required[Literal["assistant"]]
    # Content can be:
    # - str: Normal text content
    # - None: When tool_calls present
    # - list[dict[str, Any]]: Anthropic thinking blocks passthrough (for extended thinking feature)
    content: str | list[dict[str, Any]] | None
    name: str
    tool_calls: list[ToolCall]
    function_call: FunctionCall  # Deprecated


class ToolMessage(TypedDict, total=False):
    """Tool result message."""

    role: Required[Literal["tool"]]
    content: Required[str | list[TextContentPart]]
    tool_call_id: Required[str]


# Union of all message types
Message = SystemMessage | UserMessage | AssistantMessage | ToolMessage


# =============================================================================
# Request Model (OpenAI format)
# =============================================================================


class Request(BaseModel):
    """A request to an LLM (OpenAI format).

    This is what gets sent to the LLM provider. Policies can:
    - Validate the request
    - Transform parameters (e.g., clamp max_tokens)
    - Add metadata
    - Reject the request (by raising an exception)
    """

    model: str = Field(description="Model identifier (e.g., 'gpt-4', 'claude-3-5-sonnet-20241022')")
    messages: list[Message] = Field(description="Conversation messages in OpenAI format")
    max_tokens: int | None = Field(default=None, description="Maximum tokens to generate")
    temperature: float | None = Field(default=None, description="Sampling temperature")
    stream: bool = Field(default=False, description="Whether to stream the response")

    # Allow additional fields for provider-specific parameters
    model_config = {"extra": "allow"}

    @property
    def last_message(self) -> str:
        """Get the last message in the conversation."""
        if not self.messages:
            return ""
        content = self.messages[-1].get("content", "")
        # Handle multimodal content (list of content blocks)
        if isinstance(content, list):
            # Extract text from content blocks
            text_parts = [
                block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
            ]
            return " ".join(text_parts)
        return content or ""


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
    # Request model
    "Request",
]
