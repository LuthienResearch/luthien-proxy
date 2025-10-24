#!/usr/bin/env python3
"""Capture and display streaming chunks for analysis."""

import asyncio
import json
import sys

from litellm import acompletion


async def capture_streaming_chunks(messages, tools=None):
    """Make a streaming request and print each chunk."""
    print("\n" + "=" * 80)
    print(f"REQUEST: {json.dumps(messages, indent=2)}")
    if tools:
        print(f"TOOLS: {json.dumps(tools, indent=2)}")
    print("=" * 80 + "\n")

    try:
        response = await acompletion(
            model="openai/gpt-4o-mini",
            messages=messages,
            tools=tools,
            stream=True,
            max_tokens=1000,
            api_base="http://localhost:4000",
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

        print(f"\n{'=' * 80}")
        print(f"Total chunks: {chunk_count}")
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise


async def main():
    # Test 1: Simple text response
    print("\n\n### TEST 1: Simple text response ###")
    await capture_streaming_chunks([{"role": "user", "content": "Say hello in exactly 3 words"}])

    # Test 2: Tool call response
    print("\n\n### TEST 2: Single tool call ###")
    await capture_streaming_chunks(
        messages=[{"role": "user", "content": "What's the weather in San Francisco?"}],
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
    )

    # Test 3: Multiple tool calls
    print("\n\n### TEST 3: Multiple tool calls ###")
    await capture_streaming_chunks(
        messages=[{"role": "user", "content": "Get weather for SF and NYC"}],
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
    )


if __name__ == "__main__":
    asyncio.run(main())
