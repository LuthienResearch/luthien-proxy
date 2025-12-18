"""LLM type definitions.

This module provides type definitions for both OpenAI and Anthropic API formats.
Types are split into provider-specific modules for better organization.
"""

from .anthropic import (
    AnthropicContentBlock,
    AnthropicImageBlock,
    AnthropicImageSource,
    AnthropicImageSourceBase64,
    AnthropicImageSourceUrl,
    AnthropicMessage,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolResultBlock,
    AnthropicToolUseBlock,
    AnthropicUsage,
)
from .openai import (
    AssistantMessage,
    ContentPart,
    FunctionCall,
    ImageContentPart,
    ImageUrl,
    Message,
    MessageContent,
    Request,
    SystemMessage,
    TextContentPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)

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
    # Request
    "Request",
    # Anthropic image types
    "AnthropicImageSourceBase64",
    "AnthropicImageSourceUrl",
    "AnthropicImageSource",
    "AnthropicImageBlock",
    # Anthropic content blocks
    "AnthropicTextBlock",
    "AnthropicToolUseBlock",
    "AnthropicToolResultBlock",
    "AnthropicContentBlock",
    # Anthropic messages and responses
    "AnthropicMessage",
    "AnthropicUsage",
    "AnthropicResponse",
]
