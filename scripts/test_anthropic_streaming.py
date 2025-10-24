#!/usr/bin/env python3
"""Test Anthropic streaming through litellm."""

import asyncio
import json

from litellm import acompletion


async def test_anthropic_tool_streaming():
    """Test Anthropic tool call streaming."""
    print("\n=== Testing Anthropic Tool Call Streaming ===\n")

    response = await acompletion(
        model="openai/claude-sonnet-4",
        messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                },
            }
        ],
        stream=True,
        max_tokens=500,
        api_base="http://localhost:4000/v1",
        api_key="sk-luthien-dev-key",
    )

    chunk_count = 0
    async for chunk in response:
        chunk_count += 1
        chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)

        print(f"\n--- CHUNK {chunk_count} ---")
        print(json.dumps(chunk_dict, indent=2, default=str))

        # Highlight key fields
        if chunk_dict.get("choices"):
            choice = chunk_dict["choices"][0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason")

            if delta.get("role"):
                print(f"  → ROLE: {delta['role']}")
            if delta.get("content"):
                print(f"  → CONTENT: {delta['content']!r}")
            if delta.get("tool_calls"):
                print(f"  → TOOL_CALLS: {delta['tool_calls']}")
            if finish_reason:
                print(f"  → FINISH_REASON: {finish_reason}")

    print(f"\n=== Total chunks: {chunk_count} ===\n")


async def test_anthropic_text_streaming():
    """Test Anthropic text streaming."""
    print("\n=== Testing Anthropic Text Streaming ===\n")

    response = await acompletion(
        model="openai/claude-sonnet-4",
        messages=[{"role": "user", "content": "Count from 1 to 5"}],
        stream=True,
        max_tokens=500,
        api_base="http://localhost:4000/v1",
        api_key="sk-luthien-dev-key",
    )

    chunk_count = 0
    async for chunk in response:
        chunk_count += 1
        chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)

        print(f"\n--- CHUNK {chunk_count} ---")

        # Highlight key fields
        if chunk_dict.get("choices"):
            choice = chunk_dict["choices"][0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason")

            if delta.get("role"):
                print(f"  ROLE: {delta['role']}")
            if delta.get("content"):
                print(f"  CONTENT: {delta['content']!r}")
            if finish_reason:
                print(f"  FINISH_REASON: {finish_reason}")

    print(f"\n=== Total chunks: {chunk_count} ===\n")


if __name__ == "__main__":
    asyncio.run(test_anthropic_text_streaming())
    asyncio.run(test_anthropic_tool_streaming())
