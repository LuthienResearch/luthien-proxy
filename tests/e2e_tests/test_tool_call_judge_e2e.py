"""End-to-end tests validating the LLM judge tool-call policy.

The judge policy evaluates tool calls that the LLM returns and blocks harmful ones
based on the judge model's assessment. The dummy provider returns actual tool calls
for the "harmful_drop" scenario, allowing us to test blocking behavior.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from typing import Any, Dict, Mapping

import asyncpg
import httpx
import pytest
from tests.e2e_tests.helpers import E2ESettings  # noqa: E402
from tests.e2e_tests.helpers.policy_assertions import build_policy_payload  # noqa: E402

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def policy_config_path() -> str:
    return "config/policies/tool_call_judge.yaml"


@pytest.mark.asyncio
async def test_judge_policy_blocks_harmful_tool_call_non_streaming(
    use_policy,
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Verify judge policy blocks harmful tool calls in non-streaming mode."""
    payload = build_policy_payload(e2e_settings, stream=False)
    headers = {
        "Authorization": f"Bearer {e2e_settings.master_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=e2e_settings.request_timeout) as client:
        response = await client.post(
            f"{e2e_settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        )
    assert response.status_code == 200, response.text

    body = response.json()
    message = body["choices"][0]["message"]
    content = message.get("content", "")

    # Should be blocked with BLOCKED message
    assert "BLOCKED" in content
    assert "execute_sql" in content
    assert not message.get("tool_calls"), "Tool calls should be blocked"

    assert response.headers.get("x-litellm-call-id") or response.headers.get("litellm-call-id"), (
        "Expected litellm call id in headers"
    )


@pytest.mark.asyncio
async def test_judge_policy_blocks_harmful_tool_call_streaming(
    use_policy,
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Verify judge policy blocks harmful tool calls in streaming mode."""
    payload = build_policy_payload(e2e_settings, stream=True)
    headers = {
        "Authorization": f"Bearer {e2e_settings.master_key}",
        "Content-Type": "application/json",
    }

    chunks = []
    call_id = None
    async with httpx.AsyncClient(timeout=e2e_settings.request_timeout) as client:
        async with client.stream(
            "POST",
            f"{e2e_settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            assert response.status_code == 200
            call_id = response.headers.get("x-litellm-call-id") or response.headers.get("litellm-call-id")

            async for line in response.aiter_lines():
                if line.startswith("data: ") and not line.endswith("[DONE]"):
                    chunk = json.loads(line[6:])
                    chunks.append(chunk)

    assert chunks, "Expected at least one streamed chunk"

    # Find blocked chunk
    blocked_chunks = [
        chunk
        for chunk in chunks
        if "BLOCKED" in ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content", "")
    ]
    assert blocked_chunks, "Expected a blocked chunk in streaming response"

    final_chunk = blocked_chunks[-1]
    choice = (final_chunk.get("choices") or [{}])[0]
    assert choice.get("finish_reason") == "stop"
    delta = choice.get("delta") or {}
    assert "BLOCKED" in delta.get("content", "")
    assert "execute_sql" in delta.get("content", "")

    assert call_id, "Expected streaming call id in headers or body"


EXPECTED_POLICY_EVENT_TYPES = {"judge_request_sent", "judge_response_received"}


async def _consume_activity_stream(
    settings: E2ESettings,
    queue: "asyncio.Queue[dict[str, Any]]",
    stop_event: asyncio.Event,
) -> None:
    """Background task that pushes activity SSE events into a queue."""
    timeout = httpx.Timeout(connect=settings.request_timeout, read=None)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "GET",
            f"{settings.control_plane_url}/api/activity/stream",
        ) as response:
            response.raise_for_status()
            try:
                async for line in response.aiter_lines():
                    if stop_event.is_set():
                        break
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        payload = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        await queue.put(payload)
            except asyncio.CancelledError:
                pass


async def _wait_for_activity_policy_events(
    queue: "asyncio.Queue[dict[str, Any]]",
    call_id: str,
    timeout: float = 10.0,
) -> dict[str, dict[str, Any]]:
    """Collect judge policy activity events for a call from the SSE queue."""
    found: dict[str, dict[str, Any]] = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and len(found) < len(EXPECTED_POLICY_EVENT_TYPES):
        remaining = deadline - loop.time()
        try:
            event = await asyncio.wait_for(queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if not isinstance(event, dict):
            continue
        if event.get("call_id") != call_id:
            continue
        if event.get("event_type") != "policy_action":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        policy_event_type = payload.get("policy_event_type")
        if policy_event_type in EXPECTED_POLICY_EVENT_TYPES:
            found[policy_event_type] = payload
    missing = EXPECTED_POLICY_EVENT_TYPES - set(found.keys())
    if missing:
        raise AssertionError(f"Missing activity events {sorted(missing)} for call {call_id}")
    return found


def _normalize_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


async def _wait_for_policy_event_rows(
    call_id: str,
    db_url: str,
    expected_types: set[str],
    timeout: float = 10.0,
) -> Dict[str, Dict[str, Any]]:
    """Poll the policy_events table until expected event types are present."""
    conn = await asyncpg.connect(db_url)
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            rows = await conn.fetch(
                """
                SELECT event_type, metadata, policy_config
                FROM policy_events
                WHERE call_id = $1
                ORDER BY created_at ASC
                """,
                call_id,
            )
            grouped: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                event_type = row.get("event_type")
                if not isinstance(event_type, str):
                    continue
                grouped[event_type] = {
                    "metadata": _normalize_json(row.get("metadata")),
                    "policy_config": _normalize_json(row.get("policy_config")),
                }
            if expected_types.issubset(grouped.keys()):
                return grouped
            await asyncio.sleep(0.2)
        raise AssertionError(f"policy_events missing expected rows {sorted(expected_types)} for call {call_id}")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_judge_policy_emits_policy_events_and_activity_stream(
    use_policy,
    ensure_stack_ready,
    e2e_settings: E2ESettings,
) -> None:
    """Ensure judge policy emits policy events to DB and activity stream."""
    event_queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
    stop_event = asyncio.Event()
    sse_task = asyncio.create_task(_consume_activity_stream(e2e_settings, event_queue, stop_event))

    try:
        await asyncio.sleep(0.2)  # Give the activity stream time to connect

        payload = build_policy_payload(e2e_settings, stream=False)
        headers = {
            "Authorization": f"Bearer {e2e_settings.master_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=e2e_settings.request_timeout) as client:
            response = await client.post(
                f"{e2e_settings.proxy_url}/v1/chat/completions",
                headers=headers,
                json=payload,
            )

        assert response.status_code == 200, response.text
        body = response.json()
        message = body["choices"][0]["message"]
        assert "BLOCKED" in message.get("content", "")

        call_id = response.headers.get("x-litellm-call-id") or response.headers.get("litellm-call-id") or body.get("id")
        assert isinstance(call_id, str) and call_id, "Expected litellm call id in response"

        activity_events = await _wait_for_activity_policy_events(event_queue, call_id)

        request_payload = activity_events["judge_request_sent"]
        request_metadata = request_payload.get("metadata") or {}
        tool_call = request_metadata.get("tool_call")
        judge_params = request_metadata.get("judge_parameters")
        assert isinstance(tool_call, Mapping), "Expected tool_call metadata to be a mapping"
        assert tool_call.get("name") == "execute_sql"
        assert isinstance(judge_params, Mapping), "Expected judge_parameters metadata to be a mapping"
        assert judge_params.get("model")
        assert "probability_threshold" in judge_params

        response_payload = activity_events["judge_response_received"]
        response_metadata = response_payload.get("metadata") or {}
        judge_response = response_metadata.get("judge_response")
        assert isinstance(judge_response, Mapping), "Expected judge_response metadata to be a mapping"
        assert "probability" in judge_response

        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            pytest.skip("DATABASE_URL not configured; cannot verify persisted policy events")

        policy_rows = await _wait_for_policy_event_rows(call_id, db_url, EXPECTED_POLICY_EVENT_TYPES)

        db_request_meta = policy_rows["judge_request_sent"]["metadata"] or {}
        assert db_request_meta.get("tool_call", {}).get("name") == "execute_sql"
        assert "judge_parameters" in db_request_meta

        db_response_meta = policy_rows["judge_response_received"]["metadata"] or {}
        assert "judge_response" in db_response_meta
        assert "probability" in db_response_meta["judge_response"]
    finally:
        stop_event.set()
        sse_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sse_task
