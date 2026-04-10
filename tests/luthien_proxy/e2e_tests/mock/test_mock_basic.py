"""Basic mock e2e tests — verifies gateway pipeline without real Anthropic calls.

Mock server auto-started by the mock_anthropic fixture (port 18888).

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_basic.py -v
"""

import json

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import stream_response, text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}


@pytest.mark.asyncio
async def test_non_streaming_passthrough(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url, auth_headers
):
    """Gateway forwards request and returns mock JSON response unchanged."""
    mock_anthropic.enqueue(text_response("Hello from mock!"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json={**_REQUEST, "stream": False},
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert any(block.get("text") == "Hello from mock!" for block in data["content"])


@pytest.mark.asyncio
async def test_streaming_passthrough(mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url, auth_headers):
    """Gateway proxies SSE stream from mock and forwards events to client."""
    mock_anthropic.enqueue(stream_response("Streaming mock reply"))

    collected_text = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream(
            "POST",
            f"{gateway_url}/v1/messages",
            json={**_REQUEST, "stream": True},
            headers=auth_headers,
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    raw = line[len("data:") :].strip()
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            collected_text.append(delta.get("text", ""))

    assert "".join(collected_text) == "Streaming mock reply"


@pytest.mark.asyncio
async def test_default_response_when_queue_empty(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url, auth_headers
):
    """When no response is enqueued, the default 'mock response' is returned."""
    # Don't enqueue anything — should fall back to default
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json={**_REQUEST, "stream": False},
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"
    content_texts = [b.get("text", "") for b in data["content"]]
    assert any("mock response" in t for t in content_texts)
