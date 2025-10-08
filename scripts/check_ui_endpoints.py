"""Exercise control-plane UIs and APIs to verify streaming observability."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

BASE_PROXY = "http://localhost:4000"
BASE_CONTROL = "http://localhost:8081"
API_KEY = "sk-luthien-dev-key"


async def make_streaming_request() -> dict[str, Any]:
    """Issue a streaming chat request and collect minimal metadata."""
    timeout = httpx.Timeout(60.0, read=None)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{BASE_PROXY}/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": "dummy-agent",
                "messages": [{"role": "user", "content": "UI regression check"}],
                "stream": True,
            },
        )
        response.raise_for_status()
        call_id = response.headers.get("x-litellm-call-id")
        last_chunk: dict[str, Any] | None = None
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            last_chunk = chunk
            if call_id is None:
                call_id = chunk.get("id")
        if call_id is None:
            raise RuntimeError("streaming response missing call_id")
        return {"call_id": call_id, "last_chunk": last_chunk}


async def fetch_json(client: httpx.AsyncClient, path: str) -> Any:
    response = await client.get(f"{BASE_CONTROL}{path}")
    response.raise_for_status()
    return response.json()


async def wait_for_ingest(call_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        for _ in range(20):
            snapshot = await fetch_json(client, f"/api/hooks/conversation?call_id={call_id}")
            if snapshot.get("events"):
                return snapshot
            await asyncio.sleep(0.5)
    raise RuntimeError("conversation data not ingested in time")


async def collect_ui_health() -> dict[str, Any]:
    stream_result = await make_streaming_request()
    call_id = stream_result["call_id"]
    conversation = await wait_for_ingest(call_id)
    trace_id = conversation.get("trace_id")

    async with httpx.AsyncClient(timeout=15.0) as client:
        index_page = await client.get(f"{BASE_CONTROL}/ui")
        index_page.raise_for_status()
        links = sorted(set(re.findall(r'href="(/ui/[^"]+)"', index_page.text)))
        link_status = {}
        for link in links:
            resp = await client.get(f"{BASE_CONTROL}{link}")
            link_status[link] = resp.status_code

        recent_calls = await fetch_json(client, "/api/hooks/recent_call_ids?limit=20")
        trace_entries = await fetch_json(client, f"/api/hooks/trace_by_call_id?call_id={call_id}&limit=50")
        conversation_logs = await fetch_json(client, f"/api/conversation/logs?call_id={call_id}&limit=20")
        recent_traces = await fetch_json(client, "/api/hooks/recent_traces?limit=20")
        tool_logs = await fetch_json(client, f"/api/tool-calls/logs?call_id={call_id}&limit=20")
        debug_types = await fetch_json(client, "/api/debug/types")

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=5.0)) as client:
        events: list[dict[str, Any]] = []
        async with client.stream("GET", f"{BASE_CONTROL}/api/hooks/conversation/stream?call_id={call_id}") as response:
            response.raise_for_status()
            try:
                async with asyncio.timeout(5):
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        events.append(json.loads(payload))
                        break
            except TimeoutError:
                pass

    return {
        "call_id": call_id,
        "trace_id": trace_id,
        "ui_links": link_status,
        "recent_call_present": any(isinstance(item, dict) and item.get("call_id") == call_id for item in recent_calls),
        "trace_entry_count": len(trace_entries.get("entries", [])) if isinstance(trace_entries, dict) else None,
        "conversation_log_entries": len(conversation_logs) if isinstance(conversation_logs, list) else None,
        "recent_traces_count": len(recent_traces) if isinstance(recent_traces, list) else None,
        "tool_logs_count": len(tool_logs) if isinstance(tool_logs, list) else None,
        "debug_types_count": len(debug_types) if isinstance(debug_types, list) else None,
        "sse_event_seen": bool(events),
    }


async def main() -> None:
    report = await collect_ui_health()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
