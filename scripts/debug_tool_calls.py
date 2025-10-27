#!/usr/bin/env python3
# ABOUTME: Debug script to compare tool call streaming between Anthropic direct and proxy
# ABOUTME: Saves raw streaming responses to files for comparison

import json
import os
from pathlib import Path

import anthropic
import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
PROXY_API_KEY = os.getenv("PROXY_API_KEY")
PROXY_URL = "http://localhost:8000"

# Test request with a tool definition
TEST_MESSAGES = [{"role": "user", "content": "What's the weather like in San Francisco? Use the get_weather tool."}]

TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the current weather in a given location",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "The city and state, e.g. San Francisco, CA"},
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "The unit of temperature, either 'celsius' or 'fahrenheit'",
                },
            },
            "required": ["location"],
        },
    }
]


def test_anthropic_direct():
    """Test streaming request directly to Anthropic API."""
    print("=" * 80)
    print("Testing DIRECT Anthropic API request...")
    print("=" * 80)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    chunks = []

    with client.messages.stream(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=TEST_MESSAGES,
        tools=TOOLS,
    ) as stream:
        for event in stream:
            chunk_data = {"event_type": type(event).__name__, "event": str(event)}
            chunks.append(chunk_data)
            print(f"Event: {type(event).__name__}")
            print(f"  {event}")

    # Save to file
    output_file = Path("debug_anthropic_direct.json")
    with open(output_file, "w") as f:
        json.dump(chunks, f, indent=2)

    print(f"\n✓ Saved {len(chunks)} events to {output_file}")

    # Also get the final message
    final_message = stream.get_final_message()
    print("\nFinal message:")
    print(f"  Role: {final_message.role}")
    print(f"  Content blocks: {len(final_message.content)}")
    for i, block in enumerate(final_message.content):
        print(f"    Block {i}: {type(block).__name__}")
        if hasattr(block, "type"):
            print(f"      type: {block.type}")
        if hasattr(block, "name"):
            print(f"      name: {block.name}")
        if hasattr(block, "input"):
            print(f"      input: {block.input}")

    return chunks, final_message


def test_proxy():
    """Test streaming request through the proxy."""
    print("\n" + "=" * 80)
    print("Testing PROXY request...")
    print("=" * 80)

    chunks = []

    headers = {
        "Authorization": f"Bearer {PROXY_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "anthropic/claude-sonnet-4-5-20250929",
        "messages": TEST_MESSAGES,
        "tools": TOOLS,
        "max_tokens": 1024,
        "stream": True,
    }

    raw_chunks = []

    with httpx.stream(
        "POST",
        f"{PROXY_URL}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30.0,
    ) as response:
        print(f"Response status: {response.status_code}")

        for line in response.iter_lines():
            if line.strip():
                raw_chunks.append(line)
                print(f"Raw line: {line}")

                if line.startswith("data: "):
                    data_str = line[6:]  # Remove "data: " prefix

                    if data_str == "[DONE]":
                        chunks.append({"type": "done"})
                        continue

                    try:
                        data = json.loads(data_str)
                        chunks.append(data)

                        # Print summary
                        if "choices" in data:
                            for choice in data["choices"]:
                                if "delta" in choice:
                                    delta = choice["delta"]
                                    print(f"  Delta keys: {list(delta.keys())}")
                                    if "tool_calls" in delta:
                                        print(f"    Tool calls: {delta['tool_calls']}")
                    except json.JSONDecodeError as e:
                        print(f"  Error decoding JSON: {e}")
                        chunks.append({"error": str(e), "raw": data_str})

    # Save to files
    chunks_file = Path("debug_proxy_chunks.json")
    with open(chunks_file, "w") as f:
        json.dump(chunks, f, indent=2)

    raw_file = Path("debug_proxy_raw.txt")
    with open(raw_file, "w") as f:
        for line in raw_chunks:
            f.write(line + "\n")

    print(f"\n✓ Saved {len(chunks)} chunks to {chunks_file}")
    print(f"✓ Saved {len(raw_chunks)} raw lines to {raw_file}")

    return chunks, raw_chunks


def main():
    print("Starting tool call comparison test...\n")

    # Test direct Anthropic API
    try:
        direct_chunks, final_message = test_anthropic_direct()
    except Exception as e:
        print(f"❌ Error testing direct Anthropic API: {e}")
        import traceback

        traceback.print_exc()
        direct_chunks = None

    # Test proxy
    try:
        proxy_chunks, proxy_raw = test_proxy()
    except Exception as e:
        print(f"❌ Error testing proxy: {e}")
        import traceback

        traceback.print_exc()
        proxy_chunks = None

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if direct_chunks:
        print(f"Direct Anthropic: {len(direct_chunks)} events")
        tool_use_events = [c for c in direct_chunks if "ToolUse" in c.get("event_type", "")]
        print(f"  Tool use events: {len(tool_use_events)}")

    if proxy_chunks:
        print(f"Proxy: {len(proxy_chunks)} chunks")
        tool_call_chunks = [c for c in proxy_chunks if "tool_calls" in str(c)]
        print(f"  Chunks with tool_calls: {len(tool_call_chunks)}")

    print("\nRaw output files created:")
    print("  - debug_anthropic_direct.json (direct API events)")
    print("  - debug_proxy_chunks.json (proxy parsed chunks)")
    print("  - debug_proxy_raw.txt (proxy raw SSE lines)")


if __name__ == "__main__":
    main()
