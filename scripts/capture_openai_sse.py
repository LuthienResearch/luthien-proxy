#!/usr/bin/env python3
"""Capture raw OpenAI SSE streaming response for reference."""

import json
import os
from datetime import datetime

import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable required")

# Simple text request
request_payload = {
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Say hello in 3 words"}],
    "max_tokens": 20,
    "stream": True,
}

print("Capturing OpenAI streaming response...")
print(f"Request: {json.dumps(request_payload, indent=2)}\n")

raw_lines = []
parsed_chunks = []

with httpx.Client() as client:
    with client.stream(
        "POST",
        "https://api.openai.com/v1/chat/completions",
        json=request_payload,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    ) as response:
        response.raise_for_status()

        print(f"Response status: {response.status_code}")
        print(f"Content-Type: {response.headers.get('content-type')}\n")
        print("=" * 80)
        print("RAW SSE STREAM:")
        print("=" * 80)

        for line in response.iter_lines():
            raw_lines.append(line)
            print(line)

            # Parse data lines
            if line.startswith("data: "):
                data = line[6:].strip()
                if data != "[DONE]":
                    try:
                        chunk = json.loads(data)
                        parsed_chunks.append(chunk)
                    except json.JSONDecodeError:
                        pass

print("\n" + "=" * 80)
print(f"Total lines: {len(raw_lines)}")
print(f"Parsed chunks: {len(parsed_chunks)}")
print(f"Has [DONE] marker: {any('data: [DONE]' in line for line in raw_lines)}")

# Save to file
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output = {
    "timestamp": datetime.now().isoformat(),
    "url": "https://api.openai.com/v1/chat/completions",
    "request": request_payload,
    "raw_lines": raw_lines,
    "parsed_chunks": parsed_chunks,
    "has_done_marker": any("data: [DONE]" in line for line in raw_lines),
}

output_file = f"_scratch/stream_comparison/{timestamp}_openai_text.json"
os.makedirs(os.path.dirname(output_file), exist_ok=True)

with open(output_file, "w") as f:
    json.dump(output, f, indent=2)

print(f"\nSaved to: {output_file}")
