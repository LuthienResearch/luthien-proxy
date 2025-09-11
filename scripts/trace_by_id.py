#!/usr/bin/env python3

"""
Drive a request with a unique correlation id and fetch all associated hook logs.

Usage:
  TEST_MODEL=gpt-4o uv run python scripts/trace_by_id.py
"""

import asyncio
import json
import os
import uuid

import httpx


CONTROL_URL = os.getenv("CONTROL_PLANE_URL", "http://localhost:8081")
TEST_MODEL = os.getenv("TEST_MODEL", "gpt-4o")


async def main() -> int:
    cid = str(uuid.uuid4())
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Sync
        r = await client.post(
            f"{CONTROL_URL}/tests/run",
            json={
                "model": TEST_MODEL,
                "prompt": f"Hello world ({cid})",
                "stream": False,
                "correlation_id": cid,
            },
        )
        r.raise_for_status()
        print("SYNC counters:", r.json().get("counters"))

        # Stream
        r2 = await client.post(
            f"{CONTROL_URL}/tests/run",
            json={
                "model": TEST_MODEL,
                "prompt": f"Stream please ({cid})",
                "stream": True,
                "correlation_id": cid,
            },
        )
        r2.raise_for_status()
        print("STREAM counters:", r2.json().get("counters"))

        # Fetch trace
        t = await client.get(f"{CONTROL_URL}/api/hooks/trace", params={"cid": cid})
        t.raise_for_status()
        data = t.json()
        print("\nTRACE entries:")
        for e in data.get("entries", [])[:50]:
            print(json.dumps(e, indent=2)[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
