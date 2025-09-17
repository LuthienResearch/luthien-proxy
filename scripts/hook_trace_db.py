#!/usr/bin/env python3

"""
Run sync and streaming requests, then fetch litellm_hook debug logs from DB.

Environment:
- CONTROL_PLANE_URL (default http://localhost:8081)
- TEST_MODEL (a model valid for your key)
"""

import asyncio
import json
import os

import httpx

CONTROL_URL = os.getenv("CONTROL_PLANE_URL", "http://localhost:8081")
TEST_MODEL = os.getenv("TEST_MODEL", "gpt-4o")


async def run_once(stream: bool) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = {"model": TEST_MODEL, "prompt": "Trace hooks", "stream": stream}
        r = await client.post(f"{CONTROL_URL}/tests/run", json=payload)
        r.raise_for_status()
        print(("STREAM" if stream else "SYNC"), "counters:", r.json().get("counters"))


async def fetch_hook_logs(limit: int = 50) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        types = await client.get(f"{CONTROL_URL}/api/debug/types")
        types.raise_for_status()
        found = False
        for t in types.json():
            if t.get("debug_type_identifier") == "litellm_hook":
                found = True
                break
        if not found:
            raise RuntimeError("No litellm_hook entries in DB. Hook ingestion to DB failed.")
        page = await client.get(
            f"{CONTROL_URL}/api/debug/litellm_hook/page",
            params={"page": 1, "page_size": limit},
        )
        page.raise_for_status()
        items = page.json().get("items", [])
        print(f"Fetched {len(items)} litellm_hook entries:")
        for it in items[:10]:
            print(json.dumps(it, indent=2)[:1000])


async def main() -> int:
    try:
        await run_once(stream=False)
        await run_once(stream=True)
        await fetch_hook_logs(50)
        return 0
    except Exception as e:
        print("ERROR:", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
