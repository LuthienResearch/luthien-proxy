# ABOUTME: Format conversion between OpenAI and Anthropic API formats
# ABOUTME: Handles request/response/streaming transformations

"""Format converters for different LLM API formats."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    ModelResponse = Any  # LiteLLM's ModelResponse has incomplete type annotations


def anthropic_to_openai_request(data: dict) -> dict:
    """Convert Anthropic Messages API format to OpenAI format.

    Args:
        data: Request in Anthropic format

    Returns:
        Request in OpenAI format
    """
    openai_data = {
        "model": data.get("model"),
        "messages": data.get("messages", []),
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

    return {k: v for k, v in openai_data.items() if v is not None}


def openai_to_anthropic_response(response: ModelResponse) -> dict:
    """Convert OpenAI ModelResponse to Anthropic format.

    Args:
        response: ModelResponse from LiteLLM

    Returns:
        Response in Anthropic format
    """
    return {
        "id": response.id,
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": response.choices[0].message.content,
            }
        ],
        "model": response.model,
        "usage": {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
        },
        "stop_reason": (
            "end_turn" if response.choices[0].finish_reason == "stop" else response.choices[0].finish_reason
        ),
    }


def openai_chunk_to_anthropic_chunk(chunk: ModelResponse) -> dict:
    """Convert OpenAI streaming chunk to Anthropic format.

    Args:
        chunk: Streaming chunk from LiteLLM

    Returns:
        Chunk in Anthropic format
    """
    content = chunk.choices[0].delta.content or ""
    return {
        "type": "content_block_delta",
        "delta": {
            "type": "text_delta",
            "text": content,
        },
    }


__all__ = [
    "anthropic_to_openai_request",
    "openai_to_anthropic_response",
    "openai_chunk_to_anthropic_chunk",
]
