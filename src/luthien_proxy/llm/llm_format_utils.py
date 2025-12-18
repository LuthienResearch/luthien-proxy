"""Format conversion utilities for LLM API formats.

This module provides general-purpose format conversion between OpenAI and Anthropic
API formats for requests and non-streaming responses. For streaming-specific conversion,
see anthropic_sse_assembler.py.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from litellm.types.utils import Choices, ModelResponse, Usage

from luthien_proxy.llm.types import (
    AnthropicRequestDict,
    AnthropicResponseDict,
    AnthropicResponseTextBlock,
    AnthropicResponseToolUseBlock,
    AnthropicUsage,
    AssistantMessage,
    FunctionCall,
    FunctionDefinition,
    FunctionParameters,
    ImageContentPart,
    ImageUrl,
    Message,
    OpenAIRequestDict,
    SystemMessage,
    TextContentPart,
    ToolCall,
    ToolDefinition,
    ToolMessage,
    UserMessage,
)
from luthien_proxy.utils.constants import DEFAULT_LLM_MAX_TOKENS

logger = logging.getLogger(__name__)


def anthropic_to_openai_request(data: AnthropicRequestDict) -> OpenAIRequestDict:
    """Convert Anthropic Messages API format to OpenAI format.

    Args:
        data: Request in Anthropic format

    Returns:
        Request in OpenAI format
    """
    # Convert messages - handle tool results, tool use, text blocks, etc.
    openai_messages: list[Message] = []

    for msg in data.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")

        # Handle string content (simple case)
        if isinstance(content, str):
            if role == "user":
                user_msg: UserMessage = {"role": "user", "content": content}
                openai_messages.append(user_msg)
            elif role == "assistant":
                assistant_msg: AssistantMessage = {"role": "assistant", "content": content}
                openai_messages.append(assistant_msg)
            continue

        # Handle array content (tool results, tool use, text blocks, etc.)
        if isinstance(content, list):
            # Separate different content types
            # Using dict[str, Any] for tool results/uses since we access them as dicts below
            tool_results: list[dict[str, Any]] = []
            tool_uses: list[dict[str, Any]] = []
            text_parts: list[str] = []
            image_parts: list[ImageContentPart] = []

            for block in content:
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type")
                if block_type == "tool_result":
                    # Cast to dict for easier access - we know the shape from Anthropic API
                    tool_results.append(cast(dict[str, Any], block))
                elif block_type == "tool_use":
                    tool_uses.append(cast(dict[str, Any], block))
                elif block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "image":
                    # Convert Anthropic image format to OpenAI format
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/png")
                        b64_data = source.get("data", "")
                        image_url: ImageUrl = {"url": f"data:{media_type};base64,{b64_data}"}
                        image_part: ImageContentPart = {"type": "image_url", "image_url": image_url}
                        image_parts.append(image_part)
                    elif source.get("type") == "url":
                        url_image_url: ImageUrl = {"url": source.get("url", "")}
                        url_image_part: ImageContentPart = {"type": "image_url", "image_url": url_image_url}
                        image_parts.append(url_image_part)
                else:
                    logger.debug(f"Unknown content block type: {block_type}")

            # Handle tool results (user sending results back)
            if tool_results:
                for block in tool_results:
                    tool_msg: ToolMessage = {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": block.get("content", ""),
                    }
                    openai_messages.append(tool_msg)
                # This is a hacky bit of logic that fails to address the tool-rejection-message case properly.
                # Maintaining it here for the moment so we can keep iterating on it.
                # But it's broken and bad and needs to be fixed properly soon.
                if text_parts:
                    if role == "user":
                        text_user_msg: UserMessage = {"role": "user", "content": " ".join(text_parts)}
                        openai_messages.append(text_user_msg)
                    elif role == "assistant":
                        text_assistant_msg: AssistantMessage = {"role": "assistant", "content": " ".join(text_parts)}
                        openai_messages.append(text_assistant_msg)
                # end of hacky bit

            # Handle tool uses (assistant requesting tool calls)
            elif tool_uses:
                # For assistant messages with tool_use, we need to convert to OpenAI tool_calls format
                tool_calls: list[ToolCall] = []
                for block in tool_uses:
                    func_call: FunctionCall = {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    }
                    tool_call: ToolCall = {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": func_call,
                    }
                    tool_calls.append(tool_call)

                tool_use_msg: AssistantMessage = {"role": "assistant"}
                # Include text content if present
                if text_parts:
                    tool_use_msg["content"] = " ".join(text_parts)
                else:
                    tool_use_msg["content"] = None

                tool_use_msg["tool_calls"] = tool_calls
                openai_messages.append(tool_use_msg)

            # Handle regular text and/or image content
            elif text_parts or image_parts:
                # If we have images, use list format for content (OpenAI multimodal)
                if image_parts:
                    content_list: list[TextContentPart | ImageContentPart] = []
                    if text_parts:
                        text_content: TextContentPart = {"type": "text", "text": " ".join(text_parts)}
                        content_list.append(text_content)
                    content_list.extend(image_parts)
                    multimodal_msg: UserMessage = {"role": "user", "content": content_list}
                    openai_messages.append(multimodal_msg)
                else:
                    # Text only - use simple string format
                    if role == "user":
                        simple_user_msg: UserMessage = {"role": "user", "content": " ".join(text_parts)}
                        openai_messages.append(simple_user_msg)
                    elif role == "assistant":
                        simple_assistant_msg: AssistantMessage = {"role": "assistant", "content": " ".join(text_parts)}
                        openai_messages.append(simple_assistant_msg)
            # If we only have unknown block types, create an error message
            else:
                unknown_types = [block.get("type", "unknown") for block in content if isinstance(block, dict)]
                error_msg: UserMessage = {
                    "role": "user",
                    "content": f"Error: Response included only unknown block types {unknown_types}",
                }
                openai_messages.append(error_msg)
        else:
            # Unknown content format - pass through as user message
            if role == "user":
                fallback_user_msg: UserMessage = {"role": "user", "content": str(content) if content else ""}
                openai_messages.append(fallback_user_msg)
            elif role == "assistant":
                fallback_assistant_msg: AssistantMessage = {
                    "role": "assistant",
                    "content": str(content) if content else "",
                }
                openai_messages.append(fallback_assistant_msg)

    openai_data: OpenAIRequestDict = {
        "model": data.get("model", ""),
        "messages": openai_messages,
        "max_tokens": data.get("max_tokens", DEFAULT_LLM_MAX_TOKENS),
        "stream": data.get("stream", False),
    }

    if "temperature" in data:
        openai_data["temperature"] = data["temperature"]
    if "top_p" in data:
        openai_data["top_p"] = data["top_p"]

    # Handle Anthropic's system parameter
    if "system" in data:
        system_content = data["system"]
        # System can be a string or array of content blocks
        if isinstance(system_content, list):
            # Extract text from content blocks
            system_text_parts: list[str] = []
            for block in system_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    system_text_parts.append(block.get("text", ""))
            system_content = " ".join(system_text_parts) if system_text_parts else ""

        system_msg: SystemMessage = {"role": "system", "content": system_content}
        openai_data["messages"].insert(0, system_msg)

    # Handle tools (convert from Anthropic format to OpenAI format)
    if "tools" in data:
        openai_tools: list[ToolDefinition] = []
        for tool in data["tools"]:
            func_def: FunctionDefinition = {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": cast(FunctionParameters, tool.get("input_schema", {})),
            }
            tool_def: ToolDefinition = {
                "type": "function",
                "function": func_def,
            }
            openai_tools.append(tool_def)
        openai_data["tools"] = openai_tools

    # Filter out None values for cleaner output
    return cast(OpenAIRequestDict, {k: v for k, v in openai_data.items() if v is not None})


def openai_to_anthropic_response(response: ModelResponse) -> AnthropicResponseDict:
    """Convert OpenAI ModelResponse to Anthropic format.

    Args:
        response: ModelResponse from LiteLLM

    Returns:
        Response in Anthropic format
    """
    choice = response.choices[0]
    choice = cast(Choices, choice)
    message = choice.message
    content: list[AnthropicResponseTextBlock | AnthropicResponseToolUseBlock] = []

    # Add text content if present
    if message.content:
        text_block: AnthropicResponseTextBlock = {
            "type": "text",
            "text": message.content,
        }
        content.append(text_block)

    # Add tool calls if present
    if hasattr(message, "tool_calls") and message.tool_calls:
        for tool_call in message.tool_calls:
            tool_use_block: AnthropicResponseToolUseBlock = {
                "type": "tool_use",
                "id": tool_call.id or "",
                "name": tool_call.function.name or "",
                "input": (
                    json.loads(tool_call.function.arguments)
                    if isinstance(tool_call.function.arguments, str)
                    else tool_call.function.arguments
                ),
            }
            content.append(tool_use_block)

    # Map finish reasons
    finish_reason = str(response.choices[0].finish_reason)
    stop_reason_map = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
    }

    usage_raw: Usage = response.usage  # type: ignore[attr-defined] - usage is present, litellm types issue
    usage: AnthropicUsage = {
        "input_tokens": usage_raw.prompt_tokens,
        "output_tokens": usage_raw.completion_tokens,
    }

    result: AnthropicResponseDict = {
        "id": response.id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": response.model or "",
        "usage": usage,
        "stop_reason": stop_reason_map.get(finish_reason, finish_reason),
    }

    return result


__all__ = [
    "anthropic_to_openai_request",
    "openai_to_anthropic_response",
]
