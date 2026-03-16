"""Mock e2e tests for billing/quota and rate limit error handling.

Verifies that the gateway enriches upstream billing, quota, and rate limit errors
with actionable human-readable messages instead of passing through cryptic provider errors.

The Anthropic SDK retries on 5xx and 429 (default: 2 retries = 3 total attempts).
400-series errors (except 429) are NOT retried, so a single enqueued error suffices.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_billing_errors.py -v
"""

import json

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL
from tests.e2e_tests.mock_anthropic.responses import error_response, text_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}
_HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# The Anthropic SDK retries up to 2 times on 5xx/429
_SDK_MAX_ATTEMPTS = 3


# === Non-streaming billing/quota errors ===


@pytest.mark.asyncio
async def test_402_returns_billing_error_with_enriched_message(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """402 Payment Required returns billing_error type with actionable guidance."""
    mock_anthropic.enqueue(error_response(402, "billing_error", "Payment required"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code == 402
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "billing_error"
    msg = body["error"]["message"]
    # Original message preserved
    assert "Payment required" in msg
    # Actionable guidance added
    assert "billing" in msg.lower()
    assert "check" in msg.lower()


@pytest.mark.asyncio
async def test_429_quota_exhaustion_returns_billing_error(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """429 with quota keywords returns billing_error, not rate_limit_error."""
    for _ in range(_SDK_MAX_ATTEMPTS):
        mock_anthropic.enqueue(error_response(429, "rate_limit_error", "You exceeded your current quota"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "billing_error"
    msg = body["error"]["message"]
    assert "exceeded your current quota" in msg
    assert "billing" in msg.lower()


@pytest.mark.asyncio
async def test_429_rate_limit_returns_enriched_retry_message(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Plain 429 rate limit returns rate_limit_error with retry guidance."""
    for _ in range(_SDK_MAX_ATTEMPTS):
        mock_anthropic.enqueue(error_response(429, "rate_limit_error", "Rate limit exceeded"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    msg = body["error"]["message"]
    # Original message preserved
    assert "Rate limit exceeded" in msg
    # Retry guidance added
    assert "wait" in msg.lower()


@pytest.mark.asyncio
async def test_403_account_suspended_returns_billing_error(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """403 with 'suspended' keyword returns billing_error with guidance."""
    mock_anthropic.enqueue(error_response(403, "permission_error", "Your account has been suspended"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code == 403
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "billing_error"
    msg = body["error"]["message"]
    assert "suspended" in msg.lower()
    assert "billing" in msg.lower()


# === Streaming billing/quota errors ===


@pytest.mark.asyncio
async def test_streaming_402_returns_billing_error_event(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Streaming 402 returns an SSE error event with billing guidance."""
    mock_anthropic.enqueue(error_response(402, "billing_error", "Payment required"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": True},
            headers=_HEADERS,
        ) as response:
            lines = []
            async for line in response.aiter_lines():
                lines.append(line)

    # Could be pre-stream (non-200) or in-stream (200 with SSE error event)
    if response.status_code != 200:
        assert response.status_code == 402
    else:
        error_event = _find_sse_error_event(lines)
        assert error_event is not None, f"No SSE error event found in: {lines}"
        assert error_event["error"]["type"] == "billing_error"
        assert "Payment required" in error_event["error"]["message"]
        assert "billing" in error_event["error"]["message"].lower()


@pytest.mark.asyncio
async def test_streaming_429_rate_limit_returns_enriched_error_event(
    mock_anthropic: MockAnthropicServer, gateway_healthy
):
    """Streaming 429 rate limit returns an SSE error event with retry guidance."""
    for _ in range(_SDK_MAX_ATTEMPTS):
        mock_anthropic.enqueue(error_response(429, "rate_limit_error", "Rate limit exceeded"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": True},
            headers=_HEADERS,
        ) as response:
            lines = []
            async for line in response.aiter_lines():
                lines.append(line)

    if response.status_code != 200:
        assert response.status_code == 429
    else:
        error_event = _find_sse_error_event(lines)
        assert error_event is not None, f"No SSE error event found in: {lines}"
        assert error_event["error"]["type"] == "rate_limit_error"
        assert "Rate limit exceeded" in error_event["error"]["message"]
        assert "wait" in error_event["error"]["message"].lower()


@pytest.mark.asyncio
async def test_streaming_429_quota_returns_billing_error_event(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Streaming 429 with quota keywords returns billing_error SSE event."""
    for _ in range(_SDK_MAX_ATTEMPTS):
        mock_anthropic.enqueue(error_response(429, "rate_limit_error", "You exceeded your current quota"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": True},
            headers=_HEADERS,
        ) as response:
            lines = []
            async for line in response.aiter_lines():
                lines.append(line)

    if response.status_code != 200:
        assert response.status_code == 429
    else:
        error_event = _find_sse_error_event(lines)
        assert error_event is not None, f"No SSE error event found in: {lines}"
        assert error_event["error"]["type"] == "billing_error"
        assert "exceeded your current quota" in error_event["error"]["message"]


# === Recovery tests ===


@pytest.mark.asyncio
async def test_gateway_recovers_after_billing_error(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway remains responsive after a billing error — next request succeeds."""
    # 402 is not retried by SDK, so 1 enqueued error is enough
    mock_anthropic.enqueue(error_response(402, "billing_error", "Payment required"))
    mock_anthropic.enqueue(text_response("all good"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        error_resp = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )
        success_resp = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert error_resp.status_code == 402
    assert success_resp.status_code == 200
    body = success_resp.json()
    assert body["type"] == "message"
    assert any(block.get("text") == "all good" for block in body["content"])


@pytest.mark.asyncio
async def test_non_billing_403_is_not_enriched_as_billing(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """A 403 without billing keywords stays as permission_error, not billing_error."""
    mock_anthropic.enqueue(error_response(403, "permission_error", "Access denied to this resource"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code == 403
    body = response.json()
    assert body["type"] == "error"
    # Should NOT be billing_error — no billing keywords
    assert body["error"]["type"] == "permission_error"


# === Helpers ===


def _find_sse_error_event(lines: list[str]) -> dict | None:
    """Extract the error event payload from SSE lines, if present."""
    for line in lines:
        if line.startswith("data:"):
            try:
                payload = json.loads(line[len("data:") :].strip())
                if payload.get("type") == "error" and "error" in payload:
                    return payload
            except json.JSONDecodeError:
                continue
    return None
