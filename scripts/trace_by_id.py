#!/usr/bin/env python3

"""
Drive a request and fetch associated hook logs by litellm_call_id.

Usage:
  TEST_MODEL=gpt-4o uv run python scripts/trace_by_id.py
"""

import asyncio
import json
import os

import httpx


CONTROL_URL = os.getenv("CONTROL_PLANE_URL", "http://localhost:8081")
TEST_MODEL = os.getenv("TEST_MODEL", "gpt-4o")


async def main() -> int:
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Sync
        r = await client.post(
            f"{CONTROL_URL}/tests/run",
            json={
                "model": TEST_MODEL,
                "prompt": "Hello world",
                "stream": False,
            },
        )
        r.raise_for_status()
        print("SYNC counters:", r.json().get("counters"))

        # Stream
        r2 = await client.post(
            f"{CONTROL_URL}/tests/run",
            json={
                "model": TEST_MODEL,
                "prompt": "Stream please",
                "stream": True,
            },
        )
        r2.raise_for_status()
        print("STREAM counters:", r2.json().get("counters"))

        # Fetch most recent call_id and trace it
        ids = await client.get(
            f"{CONTROL_URL}/api/hooks/recent_call_ids", params={"limit": 1}
        )
        ids.raise_for_status()
        items = ids.json()
        if not items:
            print("No recent call_ids found.")
            return 0
        call_id = items[0].get("call_id")
        print("Using call_id:", call_id)
        t = await client.get(
            f"{CONTROL_URL}/api/hooks/trace_by_call_id", params={"call_id": call_id}
        )
        t.raise_for_status()
        data = t.json()
        print("\nTRACE entries:")
        for e in data.get("entries", [])[:50]:
            print(json.dumps(e, indent=2)[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
