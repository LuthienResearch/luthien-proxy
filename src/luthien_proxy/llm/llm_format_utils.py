"""Format conversion utilities for LLM API formats.

This module provides general-purpose format conversion between OpenAI and Anthropic
API formats for requests and non-streaming responses. For streaming-specific conversion,
see anthropic_sse_assembler.py.
"""

from __future__ import annotations

import json
import logging
from typing import cast

from litellm.types.utils import Choices, ModelResponse, Usage

from luthien_proxy.llm.types import (
    AnthropicImageSource,
    ImageContentPart,
    ImageUrl,
)
from luthien_proxy.utils.constants import DEFAULT_LLM_MAX_TOKENS

logger = logging.getLogger(__name__)


def anthropic_to_openai_request(data: dict) -> dict:
    """Convert Anthropic Messages API format to OpenAI format.

    Args:
        data: Request in Anthropic format

    Returns:
        Request in OpenAI format
    """
    # Convert messages - handle tool results, tool use, and text content
    openai_messages = []
    for msg in data.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")

        # Handle string content (simple case)
        if isinstance(content, str):
            openai_messages.append({"role": role, "content": content})
            continue

        # Handle array content (tool results, tool use, text blocks, etc.)
        if isinstance(content, list):
            # Separate different content types
            tool_results = []
            tool_uses = []
            text_parts = []

            image_parts: list[ImageContentPart] = []

            for block in content:
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type")
                if block_type == "tool_result":
                    tool_results.append(block)
                elif block_type == "tool_use":
                    tool_uses.append(block)
                elif block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "image":
                    # Convert Anthropic image format to OpenAI format
                    source = cast(AnthropicImageSource, block.get("source", {}))
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/png")
                        b64_data = source.get("data", "")
                        image_url: ImageUrl = {"url": f"data:{media_type};base64,{b64_data}"}
                        image_part: ImageContentPart = {"type": "image_url", "image_url": image_url}
                        image_parts.append(image_part)
                    elif source.get("type") == "url":
                        image_url = {"url": source.get("url", "")}
                        image_part = {"type": "image_url", "image_url": image_url}
                        image_parts.append(image_part)
                else:
                    logger.debug(f"Unknown content block type: {block_type}")

            # Handle tool results (user sending results back)
            if tool_results:
                for block in tool_results:
                    openai_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id"),
                            "content": block.get("content", ""),
                        }
                    )
                # This is a hacky bit of logic that fails to address the tool-rejection-message case properly.
                # Maintaining it here for the moment so we can keep iterating on it.
                # But it's broken and bad and needs to be fixed properly soon.
                if text_parts:
                    openai_messages.append(
                        {
                            "role": role,
                            "content": " ".join(text_parts),
                        }
                    )
                # end of hacky bit

            # Handle tool uses (assistant requesting tool calls)
            # These stay in the message as we're passing through Anthropic format for assistant messages
            elif tool_uses:
                # For assistant messages with tool_use, we need to convert to OpenAI tool_calls format
                tool_calls = []
                for block in tool_uses:
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        }
                    )

                openai_msg = {"role": role}
                # Include text content if present
                if text_parts:
                    openai_msg["content"] = " ".join(text_parts)
                else:
                    openai_msg["content"] = None

                openai_msg["tool_calls"] = tool_calls
                openai_messages.append(openai_msg)

            # Handle regular text and/or image content
            elif text_parts or image_parts:
                # If we have images, use list format for content (OpenAI multimodal)
                if image_parts:
                    content_list = []
                    if text_parts:
                        content_list.append({"type": "text", "text": " ".join(text_parts)})
                    content_list.extend(image_parts)
                    openai_messages.append({"role": role, "content": content_list})
                else:
                    # Text only - use simple string format
                    openai_messages.append({"role": role, "content": " ".join(text_parts)})
            # If we only have unknown block types, create an error message
            else:
                unknown_types = [block.get("type", "unknown") for block in content if isinstance(block, dict)]
                openai_messages.append(
                    {
                        "role": role,
                        "content": f"Error: Response included only unknown block types {unknown_types}",
                    }
                )
        else:
            # Unknown content format - pass through
            openai_messages.append({"role": role, "content": content})

    openai_data = {
        "model": data.get("model"),
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
            text_parts = []
            for block in system_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            system_content = " ".join(text_parts) if text_parts else ""

        openai_data["messages"].insert(
            0,
            {
                "role": "system",
                "content": system_content,
            },
        )

    # Handle tools (convert from Anthropic format to OpenAI format)
    if "tools" in data:
        openai_tools = []
        for tool in data["tools"]:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description"),
                        "parameters": tool.get("input_schema", {}),
                    },
                }
            )
        openai_data["tools"] = openai_tools

    # Handle tool_choice conversion (Anthropic format → OpenAI format)
    if "tool_choice" in data:
        tc = data["tool_choice"]
        if isinstance(tc, dict):
            tc_type = tc.get("type")
            if tc_type == "auto":
                openai_data["tool_choice"] = "auto"
            elif tc_type == "any":
                # Anthropic "any" means force tool use = OpenAI "required"
                openai_data["tool_choice"] = "required"
            elif tc_type == "tool":
                # Specific tool required
                tool_name = tc.get("name")
                if tool_name:
                    openai_data["tool_choice"] = {
                        "type": "function",
                        "function": {"name": tool_name},
                    }
        else:
            # Already in OpenAI format (string like "auto", "none", "required")
            openai_data["tool_choice"] = tc

    # Map Anthropic-specific parameter names to OpenAI equivalents.
    # LiteLLM uses OpenAI parameter names internally.
    anthropic_to_openai_param_map: dict[str, str] = {
        "stop_sequences": "stop",  # Anthropic uses stop_sequences, OpenAI uses stop
    }

    # Preserve extra parameters that weren't explicitly handled above.
    # This ensures provider-specific params like `thinking`, `metadata`,
    # etc. pass through to LiteLLM.
    handled_keys = {
        "model",
        "messages",
        "max_tokens",
        "stream",
        "temperature",
        "top_p",
        "system",
        "tools",
        "tool_choice",  # Handled above
    }
    for key, value in data.items():
        if key not in handled_keys and value is not None:
            # Map Anthropic param names to OpenAI equivalents if needed
            mapped_key = anthropic_to_openai_param_map.get(key) or key
            openai_data[mapped_key] = value

    return {k: v for k, v in openai_data.items() if v is not None}


def openai_to_anthropic_response(response: ModelResponse) -> dict:
    """Convert OpenAI ModelResponse to Anthropic format.

    Args:
        response: ModelResponse from LiteLLM

    Returns:
        Response in Anthropic format
    """
    choice = response.choices[0]
    choice = cast(Choices, choice)
    message = choice.message
    content = []

    # Add text content if present
    if message.content:
        content.append(
            {
                "type": "text",
                "text": message.content,
            }
        )

    # Add tool calls if present
    if hasattr(message, "tool_calls") and message.tool_calls:
        for tool_call in message.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "input": json.loads(tool_call.function.arguments)
                    if isinstance(tool_call.function.arguments, str)
                    else tool_call.function.arguments,
                }
            )

    # Map finish reasons
    finish_reason = str(response.choices[0].finish_reason)
    stop_reason_map = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
    }

    usage: Usage = response.usage  # type: ignore[attr-defined] - usage is present, litellm types issue

    return {
        "id": response.id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": response.model,
        "usage": {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
        },
        "stop_reason": stop_reason_map.get(finish_reason, finish_reason),
    }


__all__ = [
    "anthropic_to_openai_request",
    "openai_to_anthropic_response",
]
