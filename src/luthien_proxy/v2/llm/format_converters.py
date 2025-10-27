# ABOUTME: Format conversion between OpenAI and Anthropic API formats
# ABOUTME: Handles request/response/streaming transformations

"""Format converters for different LLM API formats."""

from __future__ import annotations

import json
from typing import cast

from litellm.types.utils import Choices, ModelResponse


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

            # Handle regular text content
            elif text_parts:
                openai_messages.append(
                    {
                        "role": role,
                        "content": " ".join(text_parts),
                    }
                )
        else:
            # Unknown content format - pass through
            openai_messages.append({"role": role, "content": content})

    openai_data = {
        "model": data.get("model"),
        "messages": openai_messages,
        "max_tokens": data.get("max_tokens", 1024),
        "stream": data.get("stream", False),
    }

    if "temperature" in data:
        openai_data["temperature"] = data["temperature"]
    if "top_p" in data:
        openai_data["top_p"] = data["top_p"]

    # Handle Anthropic's system parameter
    if "system" in data:
        openai_data["messages"].insert(
            0,
            {
                "role": "system",
                "content": data["system"],
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
    finish_reason = response.choices[0].finish_reason
    stop_reason_map = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
    }

    return {
        "id": response.id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": response.model,
        "usage": {
            "input_tokens": response.usage.prompt_tokens,  # pyright: ignore  TODO: FIX THIS
            "output_tokens": response.usage.completion_tokens,  # pyright: ignore  TODO: FIX THIS
        },
        "stop_reason": stop_reason_map.get(finish_reason, finish_reason),  # pyright: ignore TODO: FIX THIS
    }


def openai_chunk_to_anthropic_chunk(chunk: ModelResponse) -> dict:
    """Convert OpenAI streaming chunk to Anthropic format.

    Args:
        chunk: Streaming chunk from LiteLLM

    Returns:
        Anthropic format chunk
    """
    delta = chunk.choices[0].delta  # pyright: ignore TODO: FIX THIS

    # Handle tool calls
    if hasattr(delta, "tool_calls") and delta.tool_calls:
        tool_call = delta.tool_calls[0]  # Get first tool call
        has_id = hasattr(tool_call, "id") and tool_call.id
        has_args = (
            hasattr(tool_call, "function") and hasattr(tool_call.function, "arguments") and tool_call.function.arguments
        )

        # Complete tool call in one chunk (from buffered policy)
        # Send as content_block_start with the id and name
        if has_id and has_args:
            # Mark this chunk so gateway can emit additional events
            return {
                "type": "content_block_start",
                "index": getattr(tool_call, "index", 0),
                "content_block": {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "input": {},
                },
                "_complete_tool_call": True,  # Internal flag for gateway
                "_arguments": tool_call.function.arguments,
            }
        # Start of tool call (progressive streaming)
        elif has_id:
            return {
                "type": "content_block_start",
                "index": getattr(tool_call, "index", 0),
                "content_block": {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.function.name if hasattr(tool_call, "function") else "",
                    "input": {},
                },
            }
        # Delta for tool call arguments (progressive streaming)
        elif has_args:
            return {
                "type": "content_block_delta",
                "index": getattr(tool_call, "index", 0),
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": tool_call.function.arguments,
                },
            }

    # Handle text content
    content = delta.content or ""
    if content:
        return {
            "type": "content_block_delta",
            "delta": {
                "type": "text_delta",
                "text": content,
            },
        }

    # Handle finish reason (message_delta)
    finish_reason = chunk.choices[0].finish_reason
    if finish_reason:
        # Map OpenAI finish reasons to Anthropic stop reasons
        stop_reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        return {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason_map.get(finish_reason, finish_reason),
                "stop_sequence": None,
            },
        }

    # Default: empty delta
    return {
        "type": "content_block_delta",
        "delta": {
            "type": "text_delta",
            "text": "",
        },
    }


__all__ = [
    "anthropic_to_openai_request",
    "openai_to_anthropic_response",
    "openai_chunk_to_anthropic_chunk",
]
