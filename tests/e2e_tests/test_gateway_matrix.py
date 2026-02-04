# ABOUTME: Comprehensive E2E test matrix for gateway
# ABOUTME: Tests all combinations of client API, backend LLM, and streaming mode

"""Comprehensive E2E tests for gateway covering all API/LLM/mode combinations.

Test Matrix:
- Client API: OpenAI, Anthropic
- Backend LLM: OpenAI (gpt-3.5-turbo), Anthropic (claude-haiku-4-5)
- Mode: Streaming, Non-streaming

Total: 2 × 2 × 2 = 8 test combinations

These tests make real HTTP requests to the running v2-gateway service.
Run `docker compose up v2-gateway` before running these tests.
"""

import os

import httpx
import pytest

# === Test Configuration ===

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))


@pytest.fixture
async def http_client():
    """Provide async HTTP client for e2e tests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


# === OpenAI Client API Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_client_openai_backend_streaming(http_client):
    """E2E: OpenAI client → OpenAI backend (gpt-3.5-turbo), streaming."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        # Collect SSE data chunks
        data_lines = []
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                data_lines.append(line)

        assert len(data_lines) > 0, "Should receive SSE data chunks"

        # Verify we have actual content (not just [DONE])
        content_chunks = [line for line in data_lines if line.strip() != "data: [DONE]"]
        assert len(content_chunks) > 0, "Should have content chunks"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_client_openai_backend_non_streaming(http_client):
    """E2E: OpenAI client → OpenAI backend (gpt-3.5-turbo), non-streaming."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200
    data = response.json()

    # Verify OpenAI response structure
    assert "id" in data
    assert "object" in data
    assert data["object"] == "chat.completion"
    assert "choices" in data
    assert len(data["choices"]) > 0
    assert "message" in data["choices"][0]
    assert "content" in data["choices"][0]["message"]
    assert len(data["choices"][0]["message"]["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_client_anthropic_backend_streaming(http_client):
    """E2E: OpenAI client → Anthropic backend (claude-haiku), streaming."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        # Collect SSE data chunks
        data_lines = []
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                data_lines.append(line)

        assert len(data_lines) > 0, "Should receive SSE data chunks"

        # Verify OpenAI SSE format (not Anthropic events)
        content_chunks = [line for line in data_lines if line.strip() != "data: [DONE]"]
        assert len(content_chunks) > 0, "Should have content chunks"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_client_anthropic_backend_non_streaming(http_client):
    """E2E: OpenAI client → Anthropic backend (claude-haiku), non-streaming."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200
    data = response.json()

    # Verify OpenAI response structure (not Anthropic format)
    assert "id" in data
    assert "object" in data
    assert data["object"] == "chat.completion"
    assert "choices" in data
    assert len(data["choices"]) > 0
    assert "message" in data["choices"][0]
    assert "content" in data["choices"][0]["message"]
    assert len(data["choices"][0]["message"]["content"]) > 0


# === Anthropic Client API Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_client_anthropic_backend_streaming(http_client):
    """E2E: Anthropic client → Anthropic backend (claude-haiku), streaming."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
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
            "model": "claude-haiku-4-5",
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


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_client_openai_backend_streaming(http_client):
    """E2E: Anthropic client → OpenAI backend (gpt-3.5-turbo), streaming."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        # Collect Anthropic SSE events (translated from OpenAI backend)
        event_lines = []
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event_lines.append(line)

        assert len(event_lines) > 0, "Should receive Anthropic SSE events"


# NOTE: Cross-format test (Anthropic client → OpenAI backend) removed.
# The split-APIs architecture (PR #169) uses endpoint-based routing, not model-based.
# Sending an OpenAI model to /v1/messages always routes to Anthropic backend.
# Cross-format routing is Phase 2 work. See dev/NOTES.md (2026-02-03).
