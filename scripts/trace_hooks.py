#!/usr/bin/env python3

"""
Trace which LiteLLM CustomLogger hooks fire during sync and streaming requests.

Prereqs:
- Proxy running at $LITELLM_URL (default http://localhost:4000)
- Control plane running at $CONTROL_PLANE_URL (default http://localhost:8081)
- Debug callback enabled (either via litellm_config.yaml callbacks or programmatic start)

Outputs a timeline of hook invocations and includes the full request/response state captured at each hook.
"""

import asyncio
import json
import os
from typing import List, cast

import httpx

from luthien_proxy.types import JSONObject

PROXY_URL = os.getenv("LITELLM_URL", "http://localhost:4000")
CONTROL_URL = os.getenv("CONTROL_PLANE_URL", "http://localhost:8081")
MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-luthien-dev-key")
TEST_MODEL = os.getenv("TEST_MODEL", "gpt-5")


async def clear_logs(client: httpx.AsyncClient) -> None:
    await client.delete(f"{CONTROL_URL}/api/hooks/logs")


async def fetch_logs(client: httpx.AsyncClient) -> List[JSONObject]:
    r = await client.get(f"{CONTROL_URL}/api/hooks/logs", params={"limit": 200})
    r.raise_for_status()
    return cast(List[JSONObject], r.json())


def pretty(entry: JSONObject) -> str:
    hook = entry.get("hook")
    when = entry.get("when")
    t0 = entry.get("t0")
    t1 = entry.get("t1")
    kwargs = entry.get("kwargs")
    resp = entry.get("response_obj")
    return json.dumps(
        {
            "hook": hook,
            "when": when,
            "t0": t0,
            "t1": t1,
            "kwargs": kwargs,
            "response_obj": resp,
        },
        indent=2,
    )[:5000]


async def run_sync_trace(client: httpx.AsyncClient) -> None:
    print("\n=== SYNC TRACE ===")
    await clear_logs(client)
    headers = {
        "Authorization": f"Bearer {MASTER_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": TEST_MODEL,
        "messages": [{"role": "user", "content": "Trace hooks (sync): say SYNC"}],
    }
    r = await client.post(f"{PROXY_URL}/chat/completions", headers=headers, json=payload)
    print(f"Proxy status: {r.status_code}")
    logs = await fetch_logs(client)
    for e in logs:
        print(pretty(e))


async def run_stream_trace(client: httpx.AsyncClient) -> None:
    print("\n=== STREAM TRACE ===")
    await clear_logs(client)
    headers = {
        "Authorization": f"Bearer {MASTER_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": TEST_MODEL,
        "messages": [{"role": "user", "content": "Trace hooks (stream): count 1..3"}],
        "stream": True,
    }
    async with client.stream("POST", f"{PROXY_URL}/chat/completions", headers=headers, json=payload) as resp:
        print(f"Proxy status: {resp.status_code}")
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                if line[6:].strip() == "[DONE]":
                    break
    logs = await fetch_logs(client)
    for e in logs:
        print(pretty(e))


async def main() -> int:
    async with httpx.AsyncClient(timeout=20.0) as client:
        # Health checks
        try:
            cp = await client.get(f"{CONTROL_URL}/health")
            px = await client.get(f"{PROXY_URL}/test")
            assert cp.status_code == 200 and px.status_code == 200
        except Exception:
            print("Health check failed. Is the stack running?")
            return 1

        await run_sync_trace(client)
        await run_stream_trace(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
