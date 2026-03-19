# ABOUTME: E2E test matrix for gateway
# ABOUTME: Tests Anthropic client API with streaming and non-streaming modes

"""E2E tests for gateway Anthropic API combinations.

Test Matrix:
- Client API: Anthropic
- Backend LLM: Anthropic (claude-haiku-4-5)
- Mode: Streaming, Non-streaming

These tests make real HTTP requests to the running gateway service.
Run `docker compose up gateway` before running these tests.
"""

import os

import httpx
import pytest
from tests.conftest import DEFAULT_TEST_MODEL

# === Test Configuration ===

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))


@pytest.fixture
async def http_client():
    """Provide async HTTP client for e2e tests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


# === Anthropic Client API Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_client_anthropic_backend_streaming(http_client):
    """E2E: Anthropic client → Anthropic backend (claude-haiku), streaming."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        # Collect Anthropic SSE events
        event_lines = []
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event_lines.append(line)

        assert len(event_lines) > 0, "Should receive Anthropic SSE events"

        # Verify typical Anthropic event types
        event_types = [line.replace("event: ", "").strip() for line in event_lines]
        assert len(event_types) > 0, "Should have event types"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_client_anthropic_backend_non_streaming(http_client):
    """E2E: Anthropic client → Anthropic backend (claude-haiku), non-streaming."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200
    data = response.json()

    # Verify Anthropic response structure
    assert "id" in data
    assert "type" in data
    assert data["type"] == "message"
    assert "role" in data
    assert data["role"] == "assistant"
    assert "content" in data
    assert len(data["content"]) > 0
    # Content is array of content blocks in Anthropic format
    assert "text" in data["content"][0]
    assert len(data["content"][0]["text"]) > 0
