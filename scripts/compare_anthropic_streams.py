#!/usr/bin/env python3
"""Compare raw streaming responses from Anthropic API vs our proxy.

Captures the raw SSE streams from both endpoints and saves them for analysis.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx


async def capture_stream(url: str, headers: dict, payload: dict, output_file: Path) -> None:
    """Capture raw SSE stream to file.

    Args:
        url: API endpoint URL
        headers: Request headers
        payload: Request body
        output_file: Path to save the raw stream
    """
    print(f"Capturing stream from {url}...")

    events = []
    raw_lines = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                raw_lines.append(line)

                # Parse SSE format
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        events.append({"event": event_type if event_type else "message", "data": data})
                    except json.JSONDecodeError:
                        events.append({"event": "error", "raw": line})

    # Save results
    output = {
        "timestamp": datetime.now().isoformat(),
        "url": url,
        "request": payload,
        "raw_lines": raw_lines,
        "parsed_events": events,
        "total_events": len(events),
    }

    output_file.write_text(json.dumps(output, indent=2))
    print(f"Saved {len(events)} events to {output_file}")
    print(f"Raw stream had {len(raw_lines)} lines")


async def main():
    """Capture streams from both Anthropic API and our proxy."""
    # Load API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    # Test payload - simple text response
    text_payload = {
        "model": "claude-3-5-haiku-20241022",
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "Say hello in 3 words"}],
        "stream": True,
    }

    # Test payload - tool use
    tool_payload = {
        "model": "claude-3-5-haiku-20241022",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "What's the weather in San Francisco?"}],
        "tools": [
            {
                "name": "get_weather",
                "description": "Get weather for a location",
                "input_schema": {
                    "type": "object",
                    "properties": {"location": {"type": "string", "description": "City name"}},
                    "required": ["location"],
                },
            }
        ],
        "stream": True,
    }

    # Output directory
    output_dir = Path("_scratch/stream_comparison")
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Anthropic API headers
    anthropic_headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    # Proxy headers
    proxy_headers = {
        "Authorization": "Bearer sk-luthien-dev-key",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    # Capture streams
    tasks = [
        # Text responses
        capture_stream(
            "https://api.anthropic.com/v1/messages",
            anthropic_headers,
            text_payload,
            output_dir / f"{timestamp}_anthropic_text.json",
        ),
        capture_stream(
            "http://localhost:8000/v1/messages",
            proxy_headers,
            text_payload,
            output_dir / f"{timestamp}_proxy_text.json",
        ),
        # Tool use responses
        capture_stream(
            "https://api.anthropic.com/v1/messages",
            anthropic_headers,
            tool_payload,
            output_dir / f"{timestamp}_anthropic_tool.json",
        ),
        capture_stream(
            "http://localhost:8000/v1/messages",
            proxy_headers,
            tool_payload,
            output_dir / f"{timestamp}_proxy_tool.json",
        ),
    ]

    await asyncio.gather(*tasks)

    print(f"\n✓ Comparison complete! Files saved to {output_dir}/")
    print(f"  - {timestamp}_anthropic_text.json")
    print(f"  - {timestamp}_proxy_text.json")
    print(f"  - {timestamp}_anthropic_tool.json")
    print(f"  - {timestamp}_proxy_tool.json")


if __name__ == "__main__":
    asyncio.run(main())
