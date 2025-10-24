#!/usr/bin/env python3
"""ABOUTME: Test script to discover actual streaming response structures.
ABOUTME: Tests multiple tool calls, extended thinking, and other patterns."""

import asyncio
import json

from litellm import acompletion

TOOLS = [
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
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get current time for a location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    },
]


async def test_multiple_tool_calls_anthropic():
    """Test Claude with prompt designed to elicit multiple tool calls."""
    print("\n" + "=" * 80)
    print("TEST: Multiple tool calls (Anthropic)")
    print("=" * 80)

    response = await acompletion(
        model="openai/claude-sonnet-4",
        messages=[
            {
                "role": "user",
                "content": "What's the weather and current time in Tokyo and London?",
            }
        ],
        tools=TOOLS,
        stream=True,
        max_tokens=500,
        api_base="http://localhost:4000/v1",
        api_key="sk-luthien-dev-key",
    )

    await analyze_stream(response, "anthropic_multiple_tools")


async def test_multiple_tool_calls_gpt():
    """Test GPT with prompt designed to elicit multiple tool calls."""
    print("\n" + "=" * 80)
    print("TEST: Multiple tool calls (GPT)")
    print("=" * 80)

    response = await acompletion(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": "What's the weather and current time in Tokyo and London?",
            }
        ],
        tools=TOOLS,
        stream=True,
        max_tokens=500,
    )

    await analyze_stream(response, "gpt_multiple_tools")


async def test_extended_thinking_anthropic():
    """Test Claude with prompt designed to elicit extended thinking before tools."""
    print("\n" + "=" * 80)
    print("TEST: Extended thinking before tools (Anthropic)")
    print("=" * 80)

    response = await acompletion(
        model="openai/claude-sonnet-4",
        messages=[
            {
                "role": "user",
                "content": """I need to plan a trip. First, explain your reasoning about
                which cities would be best to visit in Japan in spring, then check the
                weather for your top 2 recommendations.""",
            }
        ],
        tools=TOOLS,
        stream=True,
        max_tokens=1000,
        api_base="http://localhost:4000/v1",
        api_key="sk-luthien-dev-key",
    )

    await analyze_stream(response, "anthropic_extended_thinking")


async def test_no_tools_needed():
    """Test response that could use tools but chooses not to."""
    print("\n" + "=" * 80)
    print("TEST: Tools available but not needed")
    print("=" * 80)

    response = await acompletion(
        model="openai/claude-sonnet-4",
        messages=[
            {
                "role": "user",
                "content": "What are the major cities in Japan? Just list them, don't check weather.",
            }
        ],
        tools=TOOLS,
        stream=True,
        max_tokens=500,
        api_base="http://localhost:4000/v1",
        api_key="sk-luthien-dev-key",
    )

    await analyze_stream(response, "no_tools_used")


async def analyze_stream(response, test_name):
    """Analyze a streaming response and print structure insights."""
    chunks = []
    content_chunks = []
    tool_call_chunks = []

    chunk_count = 0
    async for chunk in response:
        chunk_count += 1
        chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
        chunks.append(chunk_dict)

        if chunk_dict.get("choices"):
            choice = chunk_dict["choices"][0]
            delta = choice.get("delta", {})

            # Track what this chunk contains
            has_content = delta.get("content") is not None
            has_tool_calls = delta.get("tool_calls") is not None and delta.get("tool_calls")

            if has_content:
                content_chunks.append((chunk_count, delta.get("content")))
            if has_tool_calls:
                tool_call_chunks.append((chunk_count, delta.get("tool_calls")))

            # Print chunk summary
            parts = []
            if delta.get("role"):
                parts.append(f"role={delta['role']}")
            if has_content:
                content_preview = delta["content"][:50] if delta["content"] else '""'
                parts.append(f"content={content_preview!r}")
            if has_tool_calls:
                tc_summary = []
                for tc in delta["tool_calls"]:
                    tc_info = f"idx={tc.get('index')}"
                    if tc.get("id"):
                        tc_info += f" id={tc['id'][:15]}"
                    if tc.get("function", {}).get("name"):
                        tc_info += f" name={tc['function']['name']}"
                    if tc.get("function", {}).get("arguments"):
                        tc_info += f" args={tc['function']['arguments'][:20]!r}"
                    tc_summary.append(f"({tc_info})")
                parts.append(f"tool_calls=[{', '.join(tc_summary)}]")
            if choice.get("finish_reason"):
                parts.append(f"finish={choice['finish_reason']}")

            print(f"Chunk {chunk_count:3d}: {' | '.join(parts) if parts else '(empty delta)'}")

    # Summary
    print(f"\n--- SUMMARY for {test_name} ---")
    print(f"Total chunks: {chunk_count}")
    print(f"Content in chunks: {[n for n, _ in content_chunks]}")
    print(f"Tool calls in chunks: {[n for n, _ in tool_call_chunks]}")

    # Analyze transitions
    if content_chunks and tool_call_chunks:
        last_content_chunk = content_chunks[-1][0]
        first_tool_chunk = tool_call_chunks[0][0]
        print(f"Last content chunk: {last_content_chunk}, First tool chunk: {first_tool_chunk}")
        if last_content_chunk >= first_tool_chunk:
            print("⚠️  OVERLAPPING: Content and tool calls in same/overlapping chunks!")

    # Count unique tool calls
    tool_call_ids = set()
    tool_call_indices = set()
    for _, tcs in tool_call_chunks:
        for tc in tcs:
            if tc.get("id"):
                tool_call_ids.add(tc["id"])
            if tc.get("index") is not None:
                tool_call_indices.add(tc["index"])

    print(f"Unique tool call IDs: {len(tool_call_ids)} - {tool_call_ids}")
    print(f"Unique tool call indices: {tool_call_indices}")

    # Save chunks for later analysis
    output_file = f"/tmp/{test_name}_chunks.json"
    with open(output_file, "w") as f:
        json.dump(chunks, f, indent=2, default=str)
    print(f"Saved chunks to: {output_file}")
    print()


async def main():
    """Run all tests."""
    tests = [
        test_multiple_tool_calls_anthropic,
        test_multiple_tool_calls_gpt,
        test_extended_thinking_anthropic,
        test_no_tools_needed,
    ]

    for test in tests:
        try:
            await test()
        except Exception as e:
            print(f"❌ Test failed: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
