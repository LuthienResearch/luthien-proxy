#!/usr/bin/env python3
# ABOUTME: Capture raw SSE output from Anthropic Messages API streaming
# ABOUTME: Shows exact format that Claude Code expects

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

url = "https://api.anthropic.com/v1/messages"
headers = {
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

payload = {
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "What's the weather like in San Francisco? Use the get_weather tool."}],
    "tools": [
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
                        "description": "The unit of temperature",
                    },
                },
                "required": ["location"],
            },
        }
    ],
    "stream": True,
}

print("Capturing raw SSE from Anthropic API...")
print("=" * 80)

with httpx.stream("POST", url, headers=headers, json=payload, timeout=30.0) as response:
    print(f"Status: {response.status_code}\n")

    with open("anthropic_raw_sse.txt", "w") as f:
        for line in response.iter_lines():
            if line.strip():
                print(line)
                f.write(line + "\n")

print("\n" + "=" * 80)
print("Saved to anthropic_raw_sse.txt")
