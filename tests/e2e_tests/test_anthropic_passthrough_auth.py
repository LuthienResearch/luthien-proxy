"""E2E tests for Anthropic passthrough authentication.

Tests that clients can use their own Anthropic API keys via the
x-anthropic-api-key header, while the proxy's PROXY_API_KEY is still
required for gateway access.
"""

import os

import pytest

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_client_anthropic_key_non_streaming(gateway_healthy, http_client):
    """Client-provided Anthropic key works for non-streaming requests."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "x-anthropic-api-key": anthropic_key,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"
    assert len(data["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_invalid_client_key_rejected(gateway_healthy, http_client):
    """Invalid client Anthropic key is rejected with appropriate error."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "x-anthropic-api-key": "sk-ant-invalid-key-00000",
        },
    )

    assert response.status_code == 401


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_empty_client_key_rejected(gateway_healthy, http_client):
    """Empty x-anthropic-api-key header returns 401."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "x-anthropic-api-key": "",
        },
    )

    assert response.status_code == 401


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_fallback_to_proxy_key(gateway_healthy, http_client):
    """Without x-anthropic-api-key, the proxy's own key is used (backward compat)."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "Authorization": f"Bearer {API_KEY}",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_proxy_auth_still_required_with_client_key(gateway_healthy, http_client):
    """Proxy API key (Authorization header) is required even with x-anthropic-api-key."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5-20241022",
            "messages": [{"role": "user", "content": "Say 'hello'"}],
            "max_tokens": 10,
        },
        headers={
            "x-anthropic-api-key": anthropic_key,
            # No Authorization header
        },
    )

    assert response.status_code == 401
