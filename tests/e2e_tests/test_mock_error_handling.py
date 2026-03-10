"""Mock e2e tests for error handling — verifies gateway handles backend errors gracefully.

The gateway catches exceptions raised by the Anthropic SDK when the backend returns
4xx/5xx responses. As a result, the gateway always returns its own HTTP response
rather than propagating the raw backend status code. These tests verify:
  - The gateway does not crash on backend errors
  - The mock queue is FIFO (error consumes one slot, next request gets next slot)
  - Error and normal responses are distinguishable in the response body
  - Policies do not interfere with error handling

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_error_handling.py -v
"""

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL, policy_context
from tests.e2e_tests.mock_anthropic.responses import error_response, text_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
}
_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


@pytest.mark.asyncio
async def test_backend_500_does_not_crash_gateway(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway handles a backend 500 without crashing — returns any HTTP response."""
    mock_anthropic.enqueue(error_response(500, "internal_server_error", "Backend exploded"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    # Gateway must respond (not hang or raise a connection error).
    # The gateway wraps backend errors in its own response — status code depends on implementation.
    assert response.status_code is not None
    assert response.status_code != 0


@pytest.mark.asyncio
async def test_backend_429_does_not_crash_gateway(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway handles a backend 429 without crashing."""
    mock_anthropic.enqueue(error_response(429, "rate_limit_error", "Rate limit exceeded"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code is not None


@pytest.mark.asyncio
async def test_backend_400_does_not_crash_gateway(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway handles a backend 400 without crashing."""
    mock_anthropic.enqueue(error_response(400, "invalid_request_error", "Missing required field"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code is not None


@pytest.mark.asyncio
async def test_error_then_success_queue_order(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Mock queue is FIFO: error response consumes one slot, next request gets the next slot.

    This verifies that enqueueing an error followed by a text response results in
    the second request receiving the text response — the error doesn't corrupt or
    replay the queue.
    """
    # Use 400 (not 500) — the SDK retries on 5xx, which would consume the "recovery" slot too.
    mock_anthropic.enqueue(error_response(400, "invalid_request_error", "First request fails"))
    mock_anthropic.enqueue(text_response("recovery"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        first_response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )
        second_response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    # Second request must get the "recovery" response, not the error
    assert second_response.status_code == 200, (
        f"Expected second request to succeed, got {second_response.status_code}: {second_response.text}"
    )
    second_data = second_response.json()
    assert second_data["type"] == "message"
    assert any(block.get("text") == "recovery" for block in second_data["content"])

    # First and second responses must be different (queue was consumed in order)
    assert first_response.text != second_response.text, (
        "First and second responses are identical — queue may not have consumed the error slot"
    )


@pytest.mark.asyncio
async def test_error_response_differs_from_success(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """A backend error response is distinguishable from a normal success response.

    Even if the gateway returns 200 for both, the body should differ.
    """
    # Use 400 — the SDK retries on 5xx, which would consume both queue slots on the first request.
    mock_anthropic.enqueue(error_response(400, "invalid_request_error", "Something went wrong"))
    mock_anthropic.enqueue(text_response("normal reply"))

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

    # The success response must contain "normal reply"
    assert success_resp.status_code == 200
    assert any(block.get("text") == "normal reply" for block in success_resp.json().get("content", []))

    # The error response must NOT contain "normal reply"
    assert "normal reply" not in error_resp.text


@pytest.mark.asyncio
async def test_policy_active_during_backend_error(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway with an active policy doesn't crash when the backend errors.

    Verifies that policies don't interfere with error handling — the gateway
    remains responsive for the next request after an error.
    """
    # Use 400 — the SDK retries on 5xx, consuming both queue slots on the first request.
    mock_anthropic.enqueue(error_response(400, "invalid_request_error", "error"))
    mock_anthropic.enqueue(text_response("after error"))

    async with policy_context("luthien_proxy.policies.all_caps_policy:AllCapsPolicy", {}):
        async with httpx.AsyncClient(timeout=15.0) as client:
            # First request hits the error
            error_resp = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )
            # Second request should succeed normally
            success_resp = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": False},
                headers=_HEADERS,
            )

    # Gateway must still be responsive after the error
    assert success_resp.status_code == 200
    content = success_resp.json()["content"][0]["text"]
    # AllCapsPolicy should have uppercased "after error"
    assert content == "AFTER ERROR", f"Unexpected content after error recovery: {content!r}"
    # Error and success responses must differ
    assert error_resp.text != success_resp.text


@pytest.mark.asyncio
async def test_streaming_backend_error_gateway_responds(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway responds without hanging when the backend errors during a streaming request."""
    mock_anthropic.enqueue(error_response(500, "internal_server_error", "stream error"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": True},
            headers=_HEADERS,
        ) as response:
            # Consume the entire response to verify no hang
            lines = []
            async for line in response.aiter_lines():
                lines.append(line)

    # Gateway must have responded with something (not hung)
    assert response.status_code is not None
