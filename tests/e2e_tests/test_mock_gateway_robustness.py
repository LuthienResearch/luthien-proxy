"""Mock e2e tests for gateway robustness — input validation and concurrent requests.

Verifies that the gateway:
- Returns 400/422 for malformed or invalid requests (not 500)
- Handles concurrent requests without cross-contamination
- Enforces authentication on all endpoints

These tests do NOT require a specific policy — they test the gateway's own
input validation and request handling layer.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_gateway_robustness.py -v
"""

import asyncio

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL
from tests.e2e_tests.mock_anthropic.responses import text_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_HEADERS = {"Authorization": f"Bearer {API_KEY}"}
_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
    "stream": False,
}


# ======================================================================
# Section 1: Input validation — malformed requests
# ======================================================================


@pytest.mark.asyncio
async def test_malformed_json_returns_400(gateway_healthy):
    """Gateway returns 400 for unparseable JSON body (not 500).

    PR #336 fixed this — gateway now returns 400 instead of 500.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            content=b"not valid json {",
            headers={**_HEADERS, "Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert isinstance(response.json(), dict)


@pytest.mark.asyncio
async def test_empty_body_returns_error(gateway_healthy):
    """Gateway returns 400 or 422 for an empty request body."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            content=b"",
            headers={**_HEADERS, "Content-Type": "application/json"},
        )

    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_missing_model_field_returns_error(gateway_healthy):
    """Gateway rejects a request missing the required 'model' field."""
    payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 100,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json=payload,
            headers=_HEADERS,
        )

    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_missing_messages_field_returns_error(gateway_healthy):
    """Gateway rejects a request missing the required 'messages' field."""
    payload = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json=payload,
            headers=_HEADERS,
        )

    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_empty_messages_array_returns_error(gateway_healthy):
    """Gateway rejects a request with an empty messages array."""
    payload = {
        "model": "claude-haiku-4-5",
        "messages": [],
        "max_tokens": 100,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json=payload,
            headers=_HEADERS,
        )

    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_invalid_role_in_messages_returns_error(gateway_healthy):
    """Gateway rejects a request with an invalid message role."""
    payload = {
        "model": "claude-haiku-4-5",
        "messages": [{"role": "invalid_role", "content": "hello"}],
        "max_tokens": 100,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json=payload,
            headers=_HEADERS,
        )

    assert response.status_code in (400, 422)


# ======================================================================
# Section 2: Concurrent requests — no cross-contamination
# ======================================================================


@pytest.mark.asyncio
async def test_concurrent_requests_all_succeed(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Five concurrent requests all complete with 200 without interfering."""
    for i in range(5):
        mock_anthropic.enqueue(text_response(f"concurrent reply {i}"))

    async def make_request(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json=_BASE_REQUEST,
            headers=_HEADERS,
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        responses = await asyncio.gather(*[make_request(client) for _ in range(5)])

    for resp in responses:
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "message"


@pytest.mark.asyncio
async def test_concurrent_requests_responses_are_valid(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Three concurrent requests each return a valid Anthropic message response."""
    for i in range(3):
        mock_anthropic.enqueue(text_response(f"valid reply {i}"))

    async def make_request(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json=_BASE_REQUEST,
            headers=_HEADERS,
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        responses = await asyncio.gather(*[make_request(client) for _ in range(3)])

    for resp in responses:
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert len(data["content"]) > 0


# ======================================================================
# Section 3: Auth enforcement
# ======================================================================


@pytest.mark.asyncio
async def test_missing_auth_header_returns_401(gateway_healthy):
    """Gateway returns 401 when no Authorization header is provided."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json=_BASE_REQUEST,
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_auth_token_returns_401(gateway_healthy):
    """Gateway returns 401 for an invalid Bearer token."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json=_BASE_REQUEST,
            headers={"Authorization": "Bearer invalid-token-xyz"},
        )

    assert response.status_code == 401


# ======================================================================
# Section 4: Health endpoint
# ======================================================================


@pytest.mark.asyncio
async def test_health_endpoint_returns_200(gateway_healthy):
    """Health endpoint responds with 200."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{GATEWAY_URL}/health")

    assert response.status_code == 200
