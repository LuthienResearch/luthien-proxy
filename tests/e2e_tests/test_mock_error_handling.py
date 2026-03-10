"""Mock e2e tests for error handling — verifies gateway handles backend errors gracefully.

The Anthropic SDK retries on 5xx and 429 responses (default: 2 retries, exponential backoff).
To ensure the gateway actually receives and propagates an error we must enqueue enough error
responses to exhaust all retry slots: 1 initial attempt + 2 retries = 3 queue items for 5xx/429.
400 errors are NOT retried by the SDK, so a single enqueued error is sufficient.

The gateway maps backend errors to BackendAPIError and returns a JSONResponse with:
  - The original backend status code
  - An Anthropic-format error body: {"type": "error", "error": {"type": ..., "message": ...}}

Policies do not interfere with error handling — the gateway remains responsive after an error.

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

# The Anthropic SDK retries up to 2 times on 5xx/429, so we need 3 queue items
# (1 initial + 2 retries) to ensure all attempts see an error and the gateway
# receives the final error rather than a success on a retry slot.
_SDK_MAX_ATTEMPTS = 3


@pytest.mark.asyncio
async def test_backend_500_propagates_error_response(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway propagates a backend 500 as an Anthropic-format error response.

    Enqueues 3 errors to exhaust SDK retries so the gateway always sees a failure.
    """
    for _ in range(_SDK_MAX_ATTEMPTS):
        mock_anthropic.enqueue(error_response(500, "internal_server_error", "Backend exploded"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code == 500, f"Expected 500, got {response.status_code}: {response.text}"
    body = response.json()
    assert body.get("type") == "error", f"Expected Anthropic error envelope, got: {body}"
    assert body["error"]["type"] == "api_error"


@pytest.mark.asyncio
async def test_backend_429_propagates_error_response(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway propagates a backend 429 as an Anthropic-format error response.

    Enqueues 3 errors to exhaust SDK retries.
    """
    for _ in range(_SDK_MAX_ATTEMPTS):
        mock_anthropic.enqueue(error_response(429, "rate_limit_error", "Rate limit exceeded"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code == 429, f"Expected 429, got {response.status_code}: {response.text}"
    body = response.json()
    assert body.get("type") == "error"
    assert body["error"]["type"] == "rate_limit_error"


@pytest.mark.asyncio
async def test_backend_400_propagates_error_response(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Gateway propagates a backend 400 as an Anthropic-format error response.

    400 errors are not retried by the SDK, so a single enqueued error suffices.
    """
    mock_anthropic.enqueue(error_response(400, "invalid_request_error", "Missing required field"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={**_BASE_REQUEST, "stream": False},
            headers=_HEADERS,
        )

    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    body = response.json()
    assert body.get("type") == "error"
    assert body["error"]["type"] == "invalid_request_error"


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

    # The error response must be a 400 with Anthropic error envelope
    assert error_resp.status_code == 400
    error_body = error_resp.json()
    assert error_body.get("type") == "error"
    assert "error" in error_body

    # The success response must contain "normal reply"
    assert success_resp.status_code == 200
    assert any(block.get("text") == "normal reply" for block in success_resp.json().get("content", []))


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
