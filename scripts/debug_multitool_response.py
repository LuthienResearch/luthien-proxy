#!/usr/bin/env python3
# ABOUTME: Debug script to capture real Anthropic multi-tool-call responses
# ABOUTME: Shows streaming chunks and non-streaming format for comparison

"""Capture real Anthropic API responses with multiple tool calls.

Run with: uv run python scripts/debug_multitool_response.py
"""

import asyncio
import json

from dotenv import load_dotenv
from litellm import acompletion

load_dotenv()

# Define tools that will encourage multiple tool calls
TOOLS = [
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
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get current time for a location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City name"}},
                "required": ["location"],
            },
        },
    },
]

MESSAGES = [
    {
        "role": "user",
        "content": "Get the weather and time for both Tokyo and London",
    }
]


async def test_streaming():
    """Test streaming response with multiple tool calls."""
    print("=" * 60)
    print("STREAMING RESPONSE")
    print("=" * 60)

    response = await acompletion(
        model="anthropic/claude-sonnet-4-20250514",
        messages=MESSAGES,
        tools=TOOLS,
        stream=True,
    )

    chunks = []
    async for chunk in response:
        chunks.append(chunk)

        # Extract key info
        if chunk.choices:
            choice = chunk.choices[0]
            finish_reason = choice.finish_reason
            delta = choice.delta

            # Summarize what's in this chunk
            has_content = bool(delta.content) if delta else False
            has_tool_calls = bool(delta.tool_calls) if delta else False

            tool_info = ""
            if has_tool_calls and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if hasattr(tc, "index") else "?"
                    tc_id = tc.id if hasattr(tc, "id") else None
                    name = tc.function.name if hasattr(tc, "function") and tc.function else None
                    args = tc.function.arguments if hasattr(tc, "function") and tc.function else ""
                    tool_info = (
                        f" | tool[{idx}] id={tc_id} name={name} args='{args[:20]}...'"
                        if len(args) > 20
                        else f" | tool[{idx}] id={tc_id} name={name} args='{args}'"
                    )

            print(
                f"Chunk {len(chunks):3d}: finish_reason={finish_reason!s:12s} content={has_content!s:5s} tool_calls={has_tool_calls!s:5s}{tool_info}"
            )

    print(f"\nTotal chunks: {len(chunks)}")

    # Count finish_reason occurrences
    finish_reasons = [c.choices[0].finish_reason for c in chunks if c.choices and c.choices[0].finish_reason]
    print(f"Chunks with finish_reason: {len(finish_reasons)}")
    print(f"Finish reasons: {finish_reasons}")

    # Save chunks to file for inspection
    output_path = "debug_multitool_streaming.json"
    with open(output_path, "w") as f:
        json.dump([chunk.model_dump() for chunk in chunks], f, indent=2, default=str)
    print(f"\nSaved to {output_path}")

    return chunks


async def test_non_streaming():
    """Test non-streaming response with multiple tool calls."""
    print("\n" + "=" * 60)
    print("NON-STREAMING RESPONSE")
    print("=" * 60)

    response = await acompletion(
        model="anthropic/claude-sonnet-4-20250514",
        messages=MESSAGES,
        tools=TOOLS,
        stream=False,
    )

    print(f"finish_reason: {response.choices[0].finish_reason}")
    print(f"content: {response.choices[0].message.content}")

    if response.choices[0].message.tool_calls:
        print(f"tool_calls count: {len(response.choices[0].message.tool_calls)}")
        for i, tc in enumerate(response.choices[0].message.tool_calls):
            print(f"  [{i}] id={tc.id} name={tc.function.name} args={tc.function.arguments}")

    # Save response
    output_path = "debug_multitool_nonstreaming.json"
    with open(output_path, "w") as f:
        json.dump(response.model_dump(), f, indent=2, default=str)
    print(f"\nSaved to {output_path}")

    return response


async def main():
    await test_streaming()
    await test_non_streaming()


if __name__ == "__main__":
    asyncio.run(main())
