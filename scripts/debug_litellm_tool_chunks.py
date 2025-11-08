#!/usr/bin/env python3
"""Debug script to see what chunks LiteLLM sends for tool use."""

import asyncio
import json
import os

import litellm


async def main():
    """Stream a tool use request and print each chunk."""
    # Test payload - tool use
    request = {
        "model": "anthropic/claude-3-5-haiku-20241022",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "What's the weather in San Francisco?"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string", "description": "City name"}},
                        "required": ["location"],
                    },
                },
            }
        ],
        "stream": True,
    }

    print("=== Streaming tool use request through LiteLLM ===\n")

    response_stream = await litellm.acompletion(**request)

    chunk_num = 0
    async for chunk in response_stream:
        print(f"\n--- Chunk {chunk_num} ---")
        print(f"Type: {type(chunk)}")

        # Print the chunk as dict
        chunk_dict = chunk.model_dump()
        print(f"Model dump:\n{json.dumps(chunk_dict, indent=2, default=str)}")

        # Check for tool calls in delta
        if chunk.choices and len(chunk.choices) > 0:
            delta = chunk.choices[0].delta
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                tool_call = delta.tool_calls[0]
                print("\nTool call detected:")
                print(f"  - has 'id': {hasattr(tool_call, 'id') and tool_call.id}")
                print(f"  - id value: {getattr(tool_call, 'id', None)}")
                print(f"  - has 'function': {hasattr(tool_call, 'function')}")
                if hasattr(tool_call, "function"):
                    print(f"  - function.name: {getattr(tool_call.function, 'name', None)}")
                    print(f"  - function.arguments: {repr(getattr(tool_call.function, 'arguments', None))}")

        # Check hidden params
        if hasattr(chunk, "_hidden_params"):
            print(f"\n_hidden_params keys: {list(chunk._hidden_params.keys())}")

        chunk_num += 1

    print(f"\n=== Total chunks: {chunk_num} ===")


if __name__ == "__main__":
    # Ensure API key is set
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        print("Run: source .env")
        exit(1)

    asyncio.run(main())
