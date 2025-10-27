#!/usr/bin/env python3
# ABOUTME: Test script to see exact OpenAI streaming tool call format
# ABOUTME: Makes a direct OpenAI API call to capture the real format

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TEST_MESSAGES = [{"role": "user", "content": "What's the weather like in San Francisco? Use the get_weather tool."}]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather in a given location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "The city and state, e.g. San Francisco, CA"},
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "The unit of temperature",
                    },
                },
                "required": ["location"],
            },
        },
    }
]

print("Making streaming request to OpenAI API...")
print("=" * 80)

chunks = []
stream = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=TEST_MESSAGES,
    tools=TOOLS,
    stream=True,
)

for chunk in stream:
    chunks.append(chunk)
    print(f"\n--- Chunk {len(chunks)} ---")
    print(chunk.model_dump_json(indent=2))

# Save raw chunks
with open("openai_tool_call_chunks.json", "w") as f:
    json.dump([c.model_dump() for c in chunks], f, indent=2)

print(f"\n\n{'=' * 80}")
print(f"Saved {len(chunks)} chunks to openai_tool_call_chunks.json")
