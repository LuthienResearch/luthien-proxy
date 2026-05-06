"""Mock e2e tests for StringReplacementPolicy observability events.

Verifies that the policy emits ``policy.string_replacement.response_modified``
events through the full gateway pipeline for both non-streaming and streaming
responses, and that they are recorded in the debug events stream.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_string_replacement_events.py -v
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import stream_response, text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}

_STRING_REPLACEMENT = "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
_REPLACEMENTS = [["Anthropic", "ACME"], ["models", "widgets"]]
_RESPONSE_MODIFIED_EVENT = "policy.string_replacement.response_modified"


async def _poll_for_event(
    client: httpx.AsyncClient,
    call_id: str,
    event_type: str,
    *,
    gateway_url: str,
    admin_headers: dict,
    timeout: float = 5.0,
) -> dict:
    """Poll the debug events endpoint until ``event_type`` appears, or fail."""
    deadline = time.monotonic() + timeout
    last_payload: dict | None = None
    while time.monotonic() < deadline:
        resp = await client.get(
            f"{gateway_url}/api/debug/calls/{call_id}",
            headers=admin_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            last_payload = data
            for ev in data.get("events", []):
                if ev.get("event_type") == event_type:
                    return ev
        await asyncio.sleep(0.1)
    pytest.fail(f"Event {event_type!r} not seen for call {call_id} within {timeout}s. Last payload: {last_payload}")


@pytest.mark.asyncio
async def test_response_modified_event_emitted_for_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url: str,
    auth_headers: dict,
    admin_headers: dict,
    admin_api_key: str,
):
    """Non-streaming responses surface a response_modified event with accurate counts."""
    mock_anthropic.enqueue(text_response("Anthropic makes great models"))

    async with policy_context(
        _STRING_REPLACEMENT,
        {"replacements": _REPLACEMENTS, "match_capitalization": False},
        gateway_url=gateway_url,
        admin_api_key=admin_api_key,
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )
            assert response.status_code == 200
            assert response.json()["content"][0]["text"] == "ACME makes great widgets"

            call_id = response.headers.get("x-call-id")
            assert call_id, "No X-Call-ID header on response"

            event = await _poll_for_event(
                client,
                call_id,
                _RESPONSE_MODIFIED_EVENT,
                gateway_url=gateway_url,
                admin_headers=admin_headers,
            )

    payload = event["payload"]
    assert payload["blocks_modified"] == 1
    assert payload["total_replacements"] == 2  # Anthropic + models
    assert payload["original_length"] == len("Anthropic makes great models")
    assert payload["transformed_length"] == len("ACME makes great widgets")


@pytest.mark.asyncio
async def test_response_modified_event_emitted_for_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url: str,
    auth_headers: dict,
    admin_headers: dict,
    admin_api_key: str,
):
    """Streaming responses surface a response_modified event aggregated across the stream."""
    mock_anthropic.enqueue(stream_response("Anthropic makes models", chunks=["Anthropic ", "makes ", "models"]))

    async with policy_context(
        _STRING_REPLACEMENT,
        {"replacements": _REPLACEMENTS, "match_capitalization": False},
        gateway_url=gateway_url,
        admin_api_key=admin_api_key,
    ):
        collected: list[str] = []
        call_id: str | None = None
        async with httpx.AsyncClient(timeout=15.0) as client:
            async with client.stream(
                "POST",
                f"{gateway_url}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=auth_headers,
            ) as response:
                assert response.status_code == 200
                call_id = response.headers.get("x-call-id")
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            event = json.loads(line[len("data:") :].strip())
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                collected.append(delta.get("text", ""))

            assert call_id, "No X-Call-ID header on streaming response"
            full_text = "".join(collected)
            assert "Anthropic" not in full_text
            assert "ACME" in full_text
            assert "widgets" in full_text

            event_payload = await _poll_for_event(
                client,
                call_id,
                _RESPONSE_MODIFIED_EVENT,
                gateway_url=gateway_url,
                admin_headers=admin_headers,
            )

    payload = event_payload["payload"]
    assert payload["blocks_modified"] >= 1
    assert payload["total_replacements"] == 2  # Anthropic + models
    # Original raw chunks add up to "Anthropic makes models"; transformed length should match the emitted+flushed text.
    assert payload["original_length"] == len("Anthropic makes models")
    assert payload["transformed_length"] == len(full_text)
