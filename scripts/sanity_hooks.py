#!/usr/bin/env python3

"""
Sanity checks for Luthien Control ↔ LiteLLM integration.

Runs two tests against a running stack:
- Sync (non-streaming) chat: expect hook_pre +1 and hook_post_success +1
- Async (streaming) chat: expect hook_pre +1 and (ideally) stream_chunk > 0

Environment:
- LITELLM_URL (default: http://localhost:4000)
- CONTROL_PLANE_URL (default: http://localhost:8081)
- LITELLM_MASTER_KEY (default: sk-luthien-dev-key)
"""

import asyncio
import json
import os
from typing import Dict

import httpx

PROXY_URL = os.getenv("LITELLM_URL", "http://localhost:4000")
CONTROL_URL = os.getenv("CONTROL_PLANE_URL", "http://localhost:8081")
MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-luthien-dev-key")
TEST_MODEL = os.getenv("TEST_MODEL", "gpt-5")


async def get_counters(client: httpx.AsyncClient) -> Dict[str, int]:
    try:
        r = await client.get(f"{CONTROL_URL}/api/hooks/counters")
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"hook_pre": 0, "hook_post_success": 0, "hook_stream_chunk": 0}


async def run_sync_test(client: httpx.AsyncClient) -> bool:
    headers = {
        "Authorization": f"Bearer {MASTER_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": TEST_MODEL,
        "messages": [{"role": "user", "content": "Say SYNC_OK"}],
        "stream": False,
    }
    before = await get_counters(client)
    resp = await client.post(f"{PROXY_URL}/chat/completions", headers=headers, json=payload)
    if resp.status_code != 200:
        print(f"❌ Sync request failed: {resp.status_code} {resp.text}")
        return False
    after = await get_counters(client)
    pre_delta = after.get("hook_pre", 0) - before.get("hook_pre", 0)
    post_delta = after.get("hook_post_success", 0) - before.get("hook_post_success", 0)
    ok = pre_delta >= 1 and post_delta >= 1
    print(f"SYNC counters delta: pre={pre_delta}, post_success={post_delta}")
    if not ok:
        print("❌ Expected pre/post_success to increment by at least 1")
    else:
        print("✅ Sync sanity passed")
    return ok


async def run_stream_test(client: httpx.AsyncClient) -> bool:
    headers = {
        "Authorization": f"Bearer {MASTER_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": TEST_MODEL,
        "messages": [{"role": "user", "content": "Count 1..3 one per line"}],
        "stream": True,
    }
    before = await get_counters(client)
    try:
        async with client.stream(
            "POST",
            f"{PROXY_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30.0,
        ) as response:
            if response.status_code != 200:
                print(f"❌ Stream request failed: {response.status_code} {await response.aread()}")
                return False
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                # Consume JSON chunk, ignore content
                try:
                    _ = json.loads(data)
                except Exception:
                    pass
    except Exception as e:
        print(f"❌ Stream exception: {e}")
        return False

    after = await get_counters(client)
    pre_delta = after.get("hook_pre", 0) - before.get("hook_pre", 0)
    post_delta = after.get("hook_post_success", 0) - before.get("hook_post_success", 0)
    chunk_delta = after.get("hook_stream_chunk", 0) - before.get("hook_stream_chunk", 0)
    print(f"STREAM counters delta: pre={pre_delta}, post_success={post_delta}, stream_chunk={chunk_delta}")

    ok = pre_delta >= 1 and post_delta >= 1
    if chunk_delta == 0:
        print("ℹ️  stream_chunk not observed; this can depend on LiteLLM version/paths.")
    if ok:
        print("✅ Stream sanity passed")
    else:
        print("❌ Expected pre/post_success to increment by at least 1 in streaming path")
    return ok


async def main() -> int:
    print("🔎 Running Luthien sync/async sanity checks...\n")
    async with httpx.AsyncClient(timeout=20.0) as client:
        # Health checks
        try:
            hc_cp = await client.get(f"{CONTROL_URL}/health")
            hc_px = await client.get(f"{PROXY_URL}/test")
            if hc_cp.status_code != 200 or hc_px.status_code != 200:
                print("❌ Health checks failed. Is the stack running?")
                return 1
        except Exception as e:
            print(f"❌ Health check error: {e}")
            return 1

        print("▶️  Control-plane sync test...")
        cp_sync = await client.post(
            f"{CONTROL_URL}/tests/run",
            json={"model": TEST_MODEL, "prompt": "Say SYNC_OK", "stream": False},
        )
        if cp_sync.status_code != 200:
            print(f"❌ Control-plane sync test failed: {cp_sync.status_code} {cp_sync.text}")
            return 1
        counters = cp_sync.json().get("counters", {})
        print(f"Counters: {counters}")
        # Fail fast: require pre>=1 and post_success>=1
        sync_ok = counters.get("hook_pre", 0) >= 1 and counters.get("hook_post_success", 0) >= 1
        if not sync_ok:
            print("❌ Expected hook_post_success to increment for sync request")
            return 2

        print("▶️  Control-plane stream test...")
        cp_stream = await client.post(
            f"{CONTROL_URL}/tests/run",
            json={
                "model": TEST_MODEL,
                "prompt": "Count 1..3 one per line",
                "stream": True,
            },
        )
        if cp_stream.status_code != 200:
            print(f"❌ Control-plane stream test failed: {cp_stream.status_code} {cp_stream.text}")
            return 1
        counters = cp_stream.json().get("counters", {})
        print(f"Counters: {counters}")
        # Fail fast: require pre>=1, stream_chunk>0, and post_success>=1
        stream_ok = (
            counters.get("hook_pre", 0) >= 1
            and counters.get("hook_stream_chunk", 0) >= 1
            and counters.get("hook_post_success", 0) >= 1
        )
        if not stream_ok:
            print("❌ Expected stream_chunk and post_success to increment for stream request")
            return 2

        print("\n📊 Summary:")
        print(f"  Sync:   {'PASS' if sync_ok else 'FAIL'}")
        print(f"  Stream: {'PASS' if stream_ok else 'FAIL'}")

        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
