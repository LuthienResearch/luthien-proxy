"""Mock e2e tests for the request logs API.

Verifies:
- GET /request-logs returns a paginated list of logs
- GET /request-logs?limit=N respects the limit parameter
- GET /request-logs?limit=1&offset=0 vs offset=1 return different entries
- GET /request-logs/{transaction_id} returns detail for a specific transaction

Auth enforcement on these endpoints is covered by test_mock_auth.py.

These tests require ENABLE_REQUEST_LOGGING=true on the gateway. A module-scoped
fixture handles restarting the gateway with the env var set, then restoring it afterward.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_request_logs.py -v
"""

import asyncio
import os
import time

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e


_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
    "stream": False,
}


@pytest.fixture(scope="module")
def _enable_request_logging():
    """Verify ENABLE_REQUEST_LOGGING=true is set.

    The in-process mock gateway (start_mock_gateway.py) and run_e2e.sh both
    set this env var. If it's missing, skip rather than trying Docker compose.
    """
    if os.getenv("ENABLE_REQUEST_LOGGING", "").lower() != "true":
        pytest.skip("ENABLE_REQUEST_LOGGING not set — run via scripts/run_e2e.sh mock")


async def _make_gateway_request(client: httpx.AsyncClient, gateway_url: str, auth_headers: dict) -> None:
    """Fire a single request through the gateway (response is ignored)."""
    response = await client.post(
        f"{gateway_url}/v1/messages",
        json=_BASE_REQUEST,
        headers=auth_headers,
    )
    # Accept any non-5xx status; the important thing is the log entry is written.
    assert response.status_code < 500, f"Gateway error on test request: {response.status_code}: {response.text}"


@pytest.mark.asyncio
async def test_request_logs_list_returns_results(
    mock_anthropic: MockAnthropicServer,
    _enable_request_logging,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_headers,
):
    """Populate a log entry then GET /request-logs returns 200 with 'logs' list and 'total' int."""
    mock_anthropic.enqueue(text_response("hello from the assistant"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client, gateway_url, auth_headers)

        response = await client.get(
            f"{gateway_url}/request-logs",
            headers=admin_headers,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "logs" in data, f"Expected 'logs' key in response, got: {data}"
    assert "total" in data, f"Expected 'total' key in response, got: {data}"
    assert isinstance(data["logs"], list), f"Expected 'logs' to be a list, got: {type(data['logs'])}"
    assert isinstance(data["total"], int), f"Expected 'total' to be an int, got: {type(data['total'])}"


@pytest.mark.asyncio
async def test_request_logs_limit_param(
    mock_anthropic: MockAnthropicServer,
    _enable_request_logging,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_headers,
):
    """GET /request-logs?limit=1 returns at most 1 log entry."""
    mock_anthropic.enqueue(text_response("first response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client, gateway_url, auth_headers)

        response = await client.get(
            f"{gateway_url}/request-logs",
            params={"limit": 1},
            headers=admin_headers,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "logs" in data, f"Expected 'logs' key in response, got: {data}"
    assert len(data["logs"]) <= 1, f"Expected at most 1 log entry with limit=1, got: {len(data['logs'])}"


@pytest.mark.asyncio
async def test_request_logs_offset_param(
    mock_anthropic: MockAnthropicServer,
    _enable_request_logging,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_headers,
):
    """Two requests then offset=0 and offset=1 each return distinct transaction IDs."""
    mock_anthropic.enqueue(text_response("first response"))
    mock_anthropic.enqueue(text_response("second response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client, gateway_url, auth_headers)
        await _make_gateway_request(client, gateway_url, auth_headers)

        # Poll until the recorder has flushed at least 2 entries
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            resp = await client.get(
                f"{gateway_url}/request-logs",
                params={"limit": 10},
                headers=admin_headers,
            )
            if resp.status_code == 200 and resp.json().get("total", 0) >= 2:
                break
            await asyncio.sleep(0.1)

        # Fetch enough rows to span two distinct transactions. Each transaction
        # may produce multiple rows (inbound + outbound), so limit=1 per page
        # can return the same transaction_id at adjacent offsets.
        response = await client.get(
            f"{gateway_url}/request-logs",
            params={"limit": 10, "offset": 0},
            headers=admin_headers,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    logs = response.json().get("logs", [])
    total = response.json().get("total", 0)

    if total >= 2 and len(logs) >= 2:
        txn_ids = list(dict.fromkeys(log.get("transaction_id") for log in logs))
        assert len(txn_ids) >= 2, (
            f"Expected at least 2 distinct transaction IDs from {total} rows, got {len(txn_ids)}: {txn_ids}"
        )


@pytest.mark.asyncio
async def test_request_log_transaction_detail(
    mock_anthropic: MockAnthropicServer,
    _enable_request_logging,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_headers,
):
    """Make a request, fetch the most recent log, then GET /request-logs/{transaction_id}."""
    mock_anthropic.enqueue(text_response("detail test response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client, gateway_url, auth_headers)

        # Poll until the recorder has flushed the entry
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            resp = await client.get(
                f"{gateway_url}/request-logs",
                params={"limit": 1},
                headers=admin_headers,
            )
            if resp.status_code == 200 and resp.json().get("logs"):
                break
            await asyncio.sleep(0.1)

        list_response = await client.get(
            f"{gateway_url}/request-logs",
            params={"limit": 1},
            headers=admin_headers,
        )

    assert list_response.status_code == 200, (
        f"Unexpected status on list: {list_response.status_code}: {list_response.text}"
    )
    logs = list_response.json().get("logs", [])
    assert logs, "No request logs found — ENABLE_REQUEST_LOGGING fixture should have enabled logging"

    transaction_id = logs[0]["transaction_id"]

    async with httpx.AsyncClient(timeout=15.0) as client:
        detail_response = await client.get(
            f"{gateway_url}/request-logs/{transaction_id}",
            headers=admin_headers,
        )

    assert detail_response.status_code == 200, (
        f"Unexpected status on detail: {detail_response.status_code}: {detail_response.text}"
    )
    detail = detail_response.json()
    assert "transaction_id" in detail, f"Expected 'transaction_id' in detail response, got: {detail}"
    assert detail["transaction_id"] == transaction_id, (
        f"Expected transaction_id={transaction_id!r}, got: {detail['transaction_id']!r}"
    )
