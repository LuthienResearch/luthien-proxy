#!/usr/bin/env python3
# ABOUTME: Test non-streaming tool calls through proxy
# ABOUTME: Helps isolate if issue is streaming-specific

import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

PROXY_API_KEY = os.getenv("PROXY_API_KEY")
PROXY_URL = "http://localhost:8000"

headers = {
    "x-api-key": PROXY_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

payload = {
    "model": "anthropic/claude-sonnet-4-5-20250929",
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
    "stream": False,  # NON-STREAMING
}

print("Testing NON-STREAMING proxy /v1/messages endpoint...")
print("=" * 80)

response = httpx.post(f"{PROXY_URL}/v1/messages", headers=headers, json=payload, timeout=30.0)

print(f"Status: {response.status_code}\n")
print(json.dumps(response.json(), indent=2))

with open("proxy_nonstreaming_response.json", "w") as f:
    json.dump(response.json(), f, indent=2)

print("\n" + "=" * 80)
print("Saved to proxy_nonstreaming_response.json")
