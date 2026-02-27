"""E2E tests for request/response HTTP-level logging.

Tests the request logging feature end-to-end by making real requests
through the proxy and verifying log entries via the admin API.

Requires:
- Gateway running with ENABLE_REQUEST_LOGGING=true
- Database with migration 008 applied
- Valid ADMIN_API_KEY and PROXY_API_KEY
"""

import asyncio
import os

import httpx
import pytest

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8002")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))


@pytest.fixture
async def http_client():
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


@pytest.fixture
async def gateway_healthy():
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{GATEWAY_URL}/health")
            if resp.status_code != 200:
                pytest.skip(f"Gateway not healthy: {resp.status_code}")
        except httpx.ConnectError:
            pytest.skip(f"Gateway not running at {GATEWAY_URL}")


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {ADMIN_API_KEY}"}


@pytest.fixture
def proxy_headers():
    return {"Authorization": f"Bearer {API_KEY}"}


async def _make_proxy_request(
    client: httpx.AsyncClient,
    proxy_headers: dict[str, str],
    *,
    stream: bool = False,
) -> httpx.Response:
    """Send a minimal chat completion through the proxy."""
    return await client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers=proxy_headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "say hi"}],
            "max_tokens": 5,
            "stream": stream,
        },
    )


async def _wait_for_logs(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    *,
    min_total: int = 1,
    retries: int = 10,
    delay: float = 0.5,
    **filters: str | int | None,
) -> dict:
    """Poll the request-logs endpoint until at least min_total entries appear.

    Logs are written in background tasks, so a short poll is needed.
    """
    params: dict[str, str | int] = {"limit": 200}
    for k, v in filters.items():
        if v is not None:
            params[k] = v

    for _ in range(retries):
        resp = await client.get(f"{GATEWAY_URL}/request-logs", headers=admin_headers, params=params)
        assert resp.status_code == 200
        data = resp.json()
        if data["total"] >= min_total:
            return data
        await asyncio.sleep(delay)

    return data  # return last result even if threshold not met


# ─── Logging enabled / basic capture ─────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_request_creates_log_entries(http_client, gateway_healthy, admin_headers, proxy_headers):
    """A proxied request should produce log entries visible via the API."""
    # Get baseline count
    before = await _wait_for_logs(http_client, admin_headers, min_total=0)
    baseline = before["total"]

    # Make a request through the proxy
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    # Wait for logs to appear (background write)
    after = await _wait_for_logs(http_client, admin_headers, min_total=baseline + 2)
    assert after["total"] >= baseline + 2, f"Expected at least 2 new log entries, got {after['total'] - baseline}"


# ─── Inbound + outbound capture ──────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_inbound_and_outbound_entries(http_client, gateway_healthy, admin_headers, proxy_headers):
    """Each proxied request should create both inbound and outbound entries."""
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    await asyncio.sleep(1)

    # Fetch recent logs and find our entries
    data = await _wait_for_logs(http_client, admin_headers, min_total=2)
    logs = data["logs"]

    # Find a transaction_id that has both directions
    txn_ids = {log["transaction_id"] for log in logs}
    found_pair = False
    for txn_id in txn_ids:
        txn_logs = [entry for entry in logs if entry["transaction_id"] == txn_id]
        directions = {entry["direction"] for entry in txn_logs}
        if "inbound" in directions and "outbound" in directions:
            found_pair = True
            break

    assert found_pair, "Expected at least one transaction with both inbound and outbound entries"


# ─── Transaction detail retrieval ─────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_transaction_detail_endpoint(http_client, gateway_healthy, admin_headers, proxy_headers):
    """GET /request-logs/{transaction_id} should return inbound+outbound detail."""
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    await asyncio.sleep(1)

    # Get a recent transaction_id from the list
    data = await _wait_for_logs(http_client, admin_headers, min_total=1)
    assert len(data["logs"]) > 0
    txn_id = data["logs"][0]["transaction_id"]

    # Fetch transaction detail
    detail_resp = await http_client.get(f"{GATEWAY_URL}/request-logs/{txn_id}", headers=admin_headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    assert detail["transaction_id"] == txn_id
    assert detail["inbound"] is not None or detail["outbound"] is not None

    # Verify fields on whichever entry is present
    entry = detail["inbound"] or detail["outbound"]
    assert "id" in entry
    assert "direction" in entry
    assert "started_at" in entry
    assert entry["transaction_id"] == txn_id


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_transaction_detail_fields(http_client, gateway_healthy, admin_headers, proxy_headers):
    """Transaction detail entries should have method, url, status, timing."""
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    await asyncio.sleep(1)

    data = await _wait_for_logs(http_client, admin_headers, min_total=2)

    # Find a transaction with both sides
    txn_id = data["logs"][0]["transaction_id"]
    detail_resp = await http_client.get(f"{GATEWAY_URL}/request-logs/{txn_id}", headers=admin_headers)
    detail = detail_resp.json()

    if detail["inbound"]:
        inbound = detail["inbound"]
        assert inbound["direction"] == "inbound"
        assert inbound["http_method"] is not None
        assert inbound["url"] is not None
        assert inbound["started_at"] is not None
        # request_body should have the chat payload
        if inbound["request_body"]:
            assert "messages" in inbound["request_body"]

    if detail["outbound"]:
        outbound = detail["outbound"]
        assert outbound["direction"] == "outbound"
        assert outbound["started_at"] is not None


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_transaction_not_found(http_client, gateway_healthy, admin_headers):
    """Requesting a nonexistent transaction_id should return 404."""
    resp = await http_client.get(
        f"{GATEWAY_URL}/request-logs/nonexistent-txn-id-12345",
        headers=admin_headers,
    )
    assert resp.status_code == 404


# ─── Header sanitization ─────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_headers_are_sanitized(http_client, gateway_healthy, admin_headers, proxy_headers):
    """Auth headers in stored logs should be redacted."""
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    await asyncio.sleep(1)

    data = await _wait_for_logs(http_client, admin_headers, min_total=1)

    for log in data["logs"]:
        headers = log.get("request_headers") or {}
        for key, value in headers.items():
            if key.lower() in ("authorization", "x-api-key", "proxy-authorization"):
                assert value == "[REDACTED]", f"Header '{key}' should be redacted, got: {value}"
            # No raw API key patterns should leak
            assert "sk-luthien" not in str(value), f"API key leaked in header '{key}'"


# ─── Filtering ────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_filter_by_direction(http_client, gateway_healthy, admin_headers, proxy_headers):
    """Filter logs by direction=inbound or direction=outbound."""
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    await asyncio.sleep(1)

    inbound_data = await _wait_for_logs(http_client, admin_headers, min_total=1, direction="inbound")
    for log in inbound_data["logs"]:
        assert log["direction"] == "inbound"

    outbound_data = await _wait_for_logs(http_client, admin_headers, min_total=1, direction="outbound")
    for log in outbound_data["logs"]:
        assert log["direction"] == "outbound"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_filter_by_status(http_client, gateway_healthy, admin_headers, proxy_headers):
    """Filter logs by response status code."""
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    await asyncio.sleep(1)

    data = await _wait_for_logs(http_client, admin_headers, min_total=1, status=200)
    for log in data["logs"]:
        assert log["response_status"] == 200


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_filter_by_endpoint(http_client, gateway_healthy, admin_headers, proxy_headers):
    """Filter logs by endpoint path."""
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    await asyncio.sleep(1)

    data = await _wait_for_logs(
        http_client,
        admin_headers,
        min_total=1,
        endpoint="/v1/chat/completions",
    )
    for log in data["logs"]:
        assert log["endpoint"] == "/v1/chat/completions"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_filter_by_date_range(http_client, gateway_healthy, admin_headers, proxy_headers):
    """Filter logs using after/before date range."""
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    await asyncio.sleep(1)

    # Use a date far in the past — should still include our logs
    data = await _wait_for_logs(
        http_client,
        admin_headers,
        min_total=1,
        after="2020-01-01T00:00:00",
    )
    assert data["total"] >= 1

    # Use a date far in the future — should return nothing
    future_data = await _wait_for_logs(
        http_client,
        admin_headers,
        min_total=0,
        after="2099-01-01T00:00:00",
    )
    assert future_data["total"] == 0


# ─── Transaction linking ─────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_transaction_linking(http_client, gateway_healthy, admin_headers, proxy_headers):
    """Inbound and outbound entries for a request share a transaction_id."""
    resp = await _make_proxy_request(http_client, proxy_headers)
    assert resp.status_code == 200

    await asyncio.sleep(1)

    data = await _wait_for_logs(http_client, admin_headers, min_total=2)
    logs = data["logs"]

    # Group by transaction_id
    txn_map: dict[str, list[dict]] = {}
    for log in logs:
        txn_map.setdefault(log["transaction_id"], []).append(log)

    # Find at least one transaction with both directions
    paired = [(txn_id, entries) for txn_id, entries in txn_map.items() if len(entries) >= 2]
    assert len(paired) > 0, "No transaction found with both inbound and outbound"

    txn_id, entries = paired[0]
    directions = {e["direction"] for e in entries}
    assert "inbound" in directions, f"Transaction {txn_id} missing inbound entry"
    assert "outbound" in directions, f"Transaction {txn_id} missing outbound entry"

    # Via detail endpoint, the transaction_id should match
    detail_resp = await http_client.get(f"{GATEWAY_URL}/request-logs/{txn_id}", headers=admin_headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["transaction_id"] == txn_id
    assert detail["inbound"] is not None
    assert detail["outbound"] is not None
    assert detail["inbound"]["transaction_id"] == txn_id
    assert detail["outbound"]["transaction_id"] == txn_id


# ─── Admin auth required ─────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_request_logs_require_auth(http_client, gateway_healthy):
    """Request log endpoints should require admin authentication."""
    resp = await http_client.get(f"{GATEWAY_URL}/request-logs")
    assert resp.status_code == 403

    resp = await http_client.get(
        f"{GATEWAY_URL}/request-logs",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 403
