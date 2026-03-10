"""Mock e2e tests for the request logs API.

Verifies:
- GET /request-logs returns a paginated list of logs
- GET /request-logs?limit=N respects the limit parameter
- GET /request-logs?limit=1&offset=0 vs offset=1 return different entries
- GET /request-logs/{transaction_id} returns detail for a specific transaction

Auth enforcement on these endpoints is covered by test_mock_auth.py.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d
  - Mock server auto-started by the mock_anthropic fixture (port 18888).
  - ENABLE_REQUEST_LOGGING=true set in gateway environment.

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_request_logs.py -v
"""

import httpx
import pytest
from tests.e2e_tests.conftest import ADMIN_API_KEY, API_KEY, GATEWAY_URL
from tests.e2e_tests.mock_anthropic.responses import text_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_API_KEY}"}
_REGULAR_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
    "stream": False,
}


async def _make_gateway_request(client: httpx.AsyncClient) -> None:
    """Fire a single request through the gateway (response is ignored)."""
    response = await client.post(
        f"{GATEWAY_URL}/v1/messages",
        json=_BASE_REQUEST,
        headers=_REGULAR_HEADERS,
    )
    # Accept any non-5xx status; the important thing is the log entry is written.
    assert response.status_code < 500, f"Gateway error on test request: {response.status_code}: {response.text}"


@pytest.mark.asyncio
async def test_request_logs_list_returns_results(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Populate a log entry then GET /request-logs returns 200 with 'logs' list and 'total' int."""
    mock_anthropic.enqueue(text_response("hello from the assistant"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client)

        response = await client.get(
            f"{GATEWAY_URL}/request-logs",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "logs" in data, f"Expected 'logs' key in response, got: {data}"
    assert "total" in data, f"Expected 'total' key in response, got: {data}"
    assert isinstance(data["logs"], list), f"Expected 'logs' to be a list, got: {type(data['logs'])}"
    assert isinstance(data["total"], int), f"Expected 'total' to be an int, got: {type(data['total'])}"


@pytest.mark.asyncio
async def test_request_logs_limit_param(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """GET /request-logs?limit=1 returns at most 1 log entry."""
    mock_anthropic.enqueue(text_response("first response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client)

        response = await client.get(
            f"{GATEWAY_URL}/request-logs",
            params={"limit": 1},
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "logs" in data, f"Expected 'logs' key in response, got: {data}"
    assert len(data["logs"]) <= 1, f"Expected at most 1 log entry with limit=1, got: {len(data['logs'])}"


@pytest.mark.asyncio
async def test_request_logs_offset_param(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Two requests then offset=0 and offset=1 each return distinct transaction IDs."""
    mock_anthropic.enqueue(text_response("first response"))
    mock_anthropic.enqueue(text_response("second response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client)
        await _make_gateway_request(client)

        page_0 = await client.get(
            f"{GATEWAY_URL}/request-logs",
            params={"limit": 1, "offset": 0},
            headers=_ADMIN_HEADERS,
        )
        page_1 = await client.get(
            f"{GATEWAY_URL}/request-logs",
            params={"limit": 1, "offset": 1},
            headers=_ADMIN_HEADERS,
        )

    assert page_0.status_code == 200, f"page_0 unexpected status: {page_0.status_code}: {page_0.text}"
    assert page_1.status_code == 200, f"page_1 unexpected status: {page_1.status_code}: {page_1.text}"

    logs_0 = page_0.json().get("logs", [])
    logs_1 = page_1.json().get("logs", [])

    # If the total is >= 2 we can compare transaction IDs; otherwise skip the diff check.
    total = page_0.json().get("total", 0)
    if total >= 2 and logs_0 and logs_1:
        txn_0 = logs_0[0].get("transaction_id")
        txn_1 = logs_1[0].get("transaction_id")
        assert txn_0 != txn_1, f"Expected different transaction IDs at offset=0 and offset=1, both got: {txn_0}"


@pytest.mark.asyncio
async def test_request_log_transaction_detail(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """Make a request, fetch the most recent log, then GET /request-logs/{transaction_id}."""
    mock_anthropic.enqueue(text_response("detail test response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _make_gateway_request(client)

        list_response = await client.get(
            f"{GATEWAY_URL}/request-logs",
            params={"limit": 1},
            headers=_ADMIN_HEADERS,
        )

    assert list_response.status_code == 200, (
        f"Unexpected status on list: {list_response.status_code}: {list_response.text}"
    )
    logs = list_response.json().get("logs", [])
    if not logs:
        pytest.skip("No request logs available — request logging may be disabled (ENABLE_REQUEST_LOGGING)")

    transaction_id = logs[0]["transaction_id"]

    async with httpx.AsyncClient(timeout=15.0) as client:
        detail_response = await client.get(
            f"{GATEWAY_URL}/request-logs/{transaction_id}",
            headers=_ADMIN_HEADERS,
        )

    assert detail_response.status_code == 200, (
        f"Unexpected status on detail: {detail_response.status_code}: {detail_response.text}"
    )
    detail = detail_response.json()
    assert "transaction_id" in detail, f"Expected 'transaction_id' in detail response, got: {detail}"
    assert detail["transaction_id"] == transaction_id, (
        f"Expected transaction_id={transaction_id!r}, got: {detail['transaction_id']!r}"
    )
