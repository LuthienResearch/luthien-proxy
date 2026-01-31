"""Format conversion utilities for LLM API formats.

This module provides general-purpose format conversion between OpenAI and Anthropic
API formats for requests and non-streaming responses. For streaming-specific conversion,
see anthropic_sse_assembler.py.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, cast

from litellm.types.utils import Choices, ModelResponse, Usage

from luthien_proxy.llm.types import (
    AnthropicImageSource,
    ImageContentPart,
    ImageUrl,
)
from luthien_proxy.llm.types.anthropic import (
    AnthropicMessage,
    AnthropicRedactedThinkingBlock,
    AnthropicRequest,
    AnthropicThinkingBlock,
    AnthropicTool,
    AnthropicToolChoice,
)
from luthien_proxy.utils.constants import DEFAULT_LLM_MAX_TOKENS

logger = logging.getLogger(__name__)


@dataclass
class CategorizedBlocks:
    """Content blocks from an Anthropic message, categorized by type."""

    tool_results: list[dict] = field(default_factory=list)
    tool_uses: list[dict] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    image_parts: list[ImageContentPart] = field(default_factory=list)
    thinking_parts: list[AnthropicThinkingBlock | AnthropicRedactedThinkingBlock] = field(default_factory=list)

    def has_content(self) -> bool:
        return bool(self.tool_results or self.tool_uses or self.text_parts or self.image_parts or self.thinking_parts)


def _convert_anthropic_image_block(block: dict) -> ImageContentPart | None:
    """Convert an Anthropic image block to OpenAI format."""
    source = cast(AnthropicImageSource, block.get("source", {}))
    source_type = source.get("type")

    if source_type == "base64":
        media_type = source.get("media_type", "image/png")
        b64_data = source.get("data", "")
        image_url: ImageUrl = {"url": f"data:{media_type};base64,{b64_data}"}
        return {"type": "image_url", "image_url": image_url}

    if source_type == "url":
        image_url = {"url": source.get("url", "")}
        return {"type": "image_url", "image_url": image_url}

    return None


def _categorize_content_blocks(content: list) -> CategorizedBlocks:
    """Categorize Anthropic content blocks by type."""
    result = CategorizedBlocks()

    for block in content:
        if not isinstance(block, dict):
            continue

        block_type = block.get("type")

        if block_type == "tool_result":
            result.tool_results.append(block)
        elif block_type == "tool_use":
            result.tool_uses.append(block)
        elif block_type == "text":
            result.text_parts.append(block.get("text", ""))
        elif block_type == "thinking":
            result.thinking_parts.append(cast(AnthropicThinkingBlock, block))
        elif block_type == "redacted_thinking":
            result.thinking_parts.append(cast(AnthropicRedactedThinkingBlock, block))
        elif block_type == "image":
            image_part = _convert_anthropic_image_block(block)
            if image_part:
                result.image_parts.append(image_part)
        else:
            logger.debug(f"Unknown content block type: {block_type}")

    return result


def _build_tool_result_messages(blocks: CategorizedBlocks, role: str) -> list[dict[str, Any]]:
    """Build OpenAI messages from tool result blocks."""
    messages: list[dict[str, Any]] = []

    for block in blocks.tool_results:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": block.get("tool_use_id"),
                "content": block.get("content", ""),
            }
        )

    # WHY: This handles the case where user sends text alongside tool results (e.g., rejection messages).
    # This logic is incomplete and doesn't properly handle all tool-rejection scenarios.
    if blocks.text_parts:
        messages.append({"role": role, "content": " ".join(blocks.text_parts)})

    return messages


def _build_tool_use_message(blocks: CategorizedBlocks, role: str) -> dict[str, Any]:
    """Build an OpenAI message from tool use blocks."""
    tool_calls = [
        {
            "id": block.get("id"),
            "type": "function",
            "function": {
                "name": block.get("name"),
                "arguments": json.dumps(block.get("input", {})),
            },
        }
        for block in blocks.tool_uses
    ]

    openai_msg: dict[str, Any] = {
        "role": role,
        "content": " ".join(blocks.text_parts) if blocks.text_parts else None,
        "tool_calls": tool_calls,
    }

    if blocks.thinking_parts:
        openai_msg["thinking_blocks"] = blocks.thinking_parts

    return openai_msg


def _build_content_message(blocks: CategorizedBlocks, role: str) -> dict[str, Any]:
    """Build an OpenAI message from text, image, and/or thinking blocks."""
    openai_msg: dict[str, Any] = {"role": role}

    if blocks.image_parts:
        # Multimodal content requires list format
        content_list: list[ImageContentPart | dict[str, str]] = []
        if blocks.text_parts:
            content_list.append({"type": "text", "text": " ".join(blocks.text_parts)})
        content_list.extend(blocks.image_parts)
        openai_msg["content"] = content_list
    elif blocks.text_parts:
        openai_msg["content"] = " ".join(blocks.text_parts)
    else:
        openai_msg["content"] = None

    if blocks.thinking_parts:
        openai_msg["thinking_blocks"] = blocks.thinking_parts

    return openai_msg


def _convert_anthropic_message(msg: AnthropicMessage) -> list[dict[str, Any]]:
    """Convert a single Anthropic message to OpenAI format.

    Returns a list because tool results expand into multiple messages.
    """
    role = msg["role"]
    content = msg["content"]

    if isinstance(content, str):
        return [{"role": role, "content": content}]

    if not isinstance(content, list):
        return [{"role": role, "content": content}]

    blocks = _categorize_content_blocks(content)

    if blocks.tool_results:
        return _build_tool_result_messages(blocks, role)

    if blocks.tool_uses:
        return [_build_tool_use_message(blocks, role)]

    if blocks.text_parts or blocks.image_parts or blocks.thinking_parts:
        return [_build_content_message(blocks, role)]

    # Only unknown block types present
    unknown_types = [block.get("type", "unknown") for block in content if isinstance(block, dict)]
    return [{"role": role, "content": f"Error: Response included only unknown block types {unknown_types}"}]


def _convert_system_param(system_content: str | list) -> str:
    """Convert Anthropic system parameter to OpenAI format string."""
    if isinstance(system_content, str):
        return system_content

    text_parts = [
        block.get("text", "") for block in system_content if isinstance(block, dict) and block.get("type") == "text"
    ]
    return " ".join(text_parts) if text_parts else ""


def _convert_tools(tools: list[AnthropicTool]) -> list[dict]:
    """Convert Anthropic tools format to OpenAI format.

    Deduplicates tools by name (keeping the first occurrence) since
    Anthropic rejects requests with duplicate tool names.
    """
    seen_names: set[str] = set()
    result: list[dict] = []

    for tool in tools:
        name = tool["name"]
        if name in seen_names:
            logger.debug(f"Skipping duplicate tool: {name}")
            continue
        seen_names.add(name)
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description"),
                    "parameters": tool["input_schema"],
                },
            }
        )

    return result


def _convert_tool_choice(tc: AnthropicToolChoice) -> dict[str, Any] | str:
    """Convert Anthropic tool_choice to OpenAI format."""
    tc_type = tc["type"]

    if tc_type == "auto":
        return "auto"

    if tc_type == "any":
        return "required"

    # tc_type == "tool"
    # At this point tc is AnthropicToolChoiceTool which has a "name" field
    tool_name = cast(str, tc.get("name"))
    return {"type": "function", "function": {"name": tool_name}}


# Map Anthropic-specific parameter names to OpenAI equivalents
ANTHROPIC_TO_OPENAI_PARAM_MAP: dict[str, str] = {
    "stop_sequences": "stop",
}

HANDLED_KEYS = {
    "model",
    "messages",
    "max_tokens",
    "stream",
    "temperature",
    "top_p",
    "system",
    "tools",
    "tool_choice",
}


def anthropic_to_openai_request(data: AnthropicRequest) -> dict[str, Any]:
    """Convert Anthropic Messages API format to OpenAI format.

    Args:
        data: Request in Anthropic format

    Returns:
        Request in OpenAI format
    """
    openai_messages: list[dict[str, Any]] = []
    for msg in data.get("messages", []):
        openai_messages.extend(_convert_anthropic_message(msg))

    openai_data: dict[str, Any] = {
        "model": data.get("model"),
        "messages": openai_messages,
        "max_tokens": data.get("max_tokens", DEFAULT_LLM_MAX_TOKENS),
        "stream": data.get("stream", False),
    }

    if "temperature" in data:
        openai_data["temperature"] = data["temperature"]
    if "top_p" in data:
        openai_data["top_p"] = data["top_p"]

    if "system" in data:
        openai_data["messages"].insert(0, {"role": "system", "content": _convert_system_param(data["system"])})

    if "tools" in data:
        openai_data["tools"] = _convert_tools(data["tools"])

    if "tool_choice" in data:
        openai_data["tool_choice"] = _convert_tool_choice(data["tool_choice"])

    # Pass through extra parameters (e.g., `thinking`, `metadata`)
    for key, value in data.items():
        if key not in HANDLED_KEYS and value is not None:
            mapped_key = ANTHROPIC_TO_OPENAI_PARAM_MAP.get(key, key)
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

    # Add thinking blocks FIRST if present (required by Anthropic API)
    # LiteLLM exposes these via message.thinking_blocks as list[dict] | None
    # Two block types: "thinking" (thinking + signature) and "redacted_thinking" (data)
    if hasattr(message, "thinking_blocks") and message.thinking_blocks:
        for block in message.thinking_blocks:
            block_type = block.get("type", "thinking")
            if block_type == "redacted_thinking":
                content.append(
                    {
                        "type": "redacted_thinking",
                        "data": block.get("data", ""),
                    }
                )
            else:
                content.append(
                    {
                        "type": "thinking",
                        "thinking": block.get("thinking", ""),
                        "signature": block.get("signature", ""),
                    }
                )

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


def deduplicate_tools(tools: list[dict]) -> list[dict]:
    """Deduplicate tools by function name, keeping the first occurrence.

    This is necessary because Anthropic rejects requests with duplicate tool names,
    while OpenAI silently accepts them. Claude Code and other clients may send
    duplicates (e.g., during /compact operations).

    Args:
        tools: List of OpenAI-format tools (with type: "function" and function.name)

    Returns:
        Deduplicated list of tools
    """
    seen_names: set[str] = set()
    result: list[dict] = []

    for tool in tools:
        # Handle OpenAI tool format: {"type": "function", "function": {"name": "..."}}
        if tool.get("type") == "function" and "function" in tool:
            name = tool["function"].get("name")
            if name and name in seen_names:
                logger.debug(f"Skipping duplicate tool: {name}")
                continue
            if name:
                seen_names.add(name)
        result.append(tool)

    return result


__all__ = [
    "anthropic_to_openai_request",
    "openai_to_anthropic_response",
    "deduplicate_tools",
]
