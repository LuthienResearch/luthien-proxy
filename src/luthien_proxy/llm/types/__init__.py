"""LLM type definitions for OpenAI and Anthropic formats.

This package provides strict type definitions for:
- OpenAI Chat Completion API message types
- Anthropic Messages API content and message types
- Request model for policy processing
"""

from .anthropic import (
    AnthropicAssistantMessage,
    AnthropicContentBlock,
    AnthropicImageBlock,
    AnthropicImageSource,
    AnthropicImageSourceBase64,
    AnthropicImageSourceUrl,
    AnthropicMessage,
    AnthropicResponse,
    AnthropicSystemBlock,
    AnthropicSystemContent,
    AnthropicTextBlock,
    AnthropicTool,
    AnthropicToolResultBlock,
    AnthropicToolUseBlock,
    AnthropicUsage,
    AnthropicUserMessage,
)
from .openai import (
    AssistantMessage,
    ContentPart,
    DeveloperMessage,
    FunctionCall,
    ImageContentPart,
    ImageUrl,
    Message,
    MessageContent,
    RedactedThinkingBlock,
    Request,
    SystemMessage,
    TextContentPart,
    ThinkingBlock,
    ThinkingBlockType,
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
    # Thinking blocks (LiteLLM extension)
    "ThinkingBlock",
    "RedactedThinkingBlock",
    "ThinkingBlockType",
    # OpenAI messages
    "SystemMessage",
    "DeveloperMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolMessage",
    "FunctionCall",
    "ToolCall",
    "Message",
    # Request model
    "Request",
    # Anthropic content blocks
    "AnthropicTextBlock",
    "AnthropicImageSourceBase64",
    "AnthropicImageSourceUrl",
    "AnthropicImageSource",
    "AnthropicImageBlock",
    "AnthropicToolUseBlock",
    "AnthropicToolResultBlock",
    "AnthropicContentBlock",
    # Anthropic messages
    "AnthropicUserMessage",
    "AnthropicAssistantMessage",
    "AnthropicMessage",
    # Anthropic system content
    "AnthropicSystemBlock",
    "AnthropicSystemContent",
    # Anthropic tools
    "AnthropicTool",
    # Anthropic response types
    "AnthropicUsage",
    "AnthropicResponse",
]
