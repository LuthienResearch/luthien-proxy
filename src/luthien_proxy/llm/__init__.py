"""LLM integration using LiteLLM as a library."""

from .llm_format_utils import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
)
from .types import (
    # Anthropic types (for format conversion)
    AnthropicImageBlock,
    AnthropicImageSource,
    # OpenAI types
    AssistantMessage,
    ContentPart,
    FunctionCall,
    ImageContentPart,
    ImageUrl,
    Message,
    MessageContent,
    SystemMessage,
    TextContentPart,
    ToolCall,
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
    "ImageContentPart",
    "ImageUrl",
    "Message",
    "MessageContent",
    "SystemMessage",
    "TextContentPart",
    "ToolCall",
    "ToolMessage",
    "UserMessage",
    # Anthropic types (for format conversion)
    "AnthropicImageBlock",
    "AnthropicImageSource",
]
