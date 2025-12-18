"""LLM integration using LiteLLM as a library."""

from .llm_format_utils import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
)
from .types import (
    # Anthropic request types
    AnthropicContentBlock,
    AnthropicImageBlock,
    AnthropicImageSource,
    AnthropicMessage,
    AnthropicRequestDict,
    # Anthropic response types
    AnthropicResponseContentBlock,
    AnthropicResponseDict,
    AnthropicResponseTextBlock,
    AnthropicResponseToolUseBlock,
    AnthropicTextBlock,
    AnthropicToolDefinition,
    AnthropicToolResultBlock,
    AnthropicToolUseBlock,
    AnthropicUsage,
    # OpenAI message types
    AssistantMessage,
    ContentPart,
    FunctionCall,
    FunctionDefinition,
    FunctionParameters,
    ImageContentPart,
    ImageUrl,
    Message,
    MessageContent,
    OpenAIRequestDict,
    SystemMessage,
    TextContentPart,
    ToolCall,
    ToolDefinition,
    ToolMessage,
    UserMessage,
)

__all__ = [
    # Format converters
    "anthropic_to_openai_request",
    "openai_to_anthropic_response",
    # OpenAI message types
    "AssistantMessage",
    "ContentPart",
    "FunctionCall",
    "FunctionDefinition",
    "FunctionParameters",
    "ImageContentPart",
    "ImageUrl",
    "Message",
    "MessageContent",
    "OpenAIRequestDict",
    "SystemMessage",
    "TextContentPart",
    "ToolCall",
    "ToolDefinition",
    "ToolMessage",
    "UserMessage",
    # Anthropic request types
    "AnthropicContentBlock",
    "AnthropicImageBlock",
    "AnthropicImageSource",
    "AnthropicMessage",
    "AnthropicRequestDict",
    "AnthropicTextBlock",
    "AnthropicToolDefinition",
    "AnthropicToolResultBlock",
    "AnthropicToolUseBlock",
    # Anthropic response types
    "AnthropicResponseContentBlock",
    "AnthropicResponseDict",
    "AnthropicResponseTextBlock",
    "AnthropicResponseToolUseBlock",
    "AnthropicUsage",
]
