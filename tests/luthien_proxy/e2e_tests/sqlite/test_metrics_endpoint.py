"""sqlite_e2e tests for the /metrics Prometheus endpoint.

Boots a self-contained SQLite gateway + mock Anthropic server and verifies:
1. /metrics returns 200 with Prometheus content type
2. After a request through the gateway, metric families appear in the output
3. Metric names match what the changelog advertises (no OTel suffix mangling)

Run:  uv run pytest tests/luthien_proxy/e2e_tests/sqlite/test_metrics_endpoint.py -v --timeout=60
"""

import asyncio
import os
import socket
import tempfile
import threading
import time

import httpx
import pytest
import uvicorn
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

from luthien_proxy.main import create_app
from luthien_proxy.settings import clear_settings_cache
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations

pytestmark = pytest.mark.sqlite_e2e

_API_KEY = "test-metrics-key"
_ADMIN_KEY = "test-metrics-admin-key"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_server():
    server = MockAnthropicServer(port=_free_port())
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def gateway_url(mock_server):
    port = _free_port()
    tmp_dir = tempfile.mkdtemp(prefix="luthien_metrics_e2e_")
    db_path = os.path.join(tmp_dir, "test.db")
    db_pool = DatabasePool(f"sqlite:///{db_path}")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(check_migrations(db_pool))

    old_env = {}
    for k, v in {
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{mock_server.port}",
        "ANTHROPIC_API_KEY": "mock-key",
    }.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v

    clear_settings_cache()
    app = create_app(
        api_key=_API_KEY,
        admin_key=_ADMIN_KEY,
        db_pool=db_pool,
        redis_client=None,
        startup_policy_path="config/policy_config.yaml",
        policy_source="db-fallback-file",
    )

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="metrics-gateway")
    thread.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Metrics gateway did not start")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)
    loop.run_until_complete(db_pool.close())
    loop.close()
    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


async def test_metrics_returns_200_before_traffic(gateway_url):
    """GET /metrics works even before any requests flow through."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{gateway_url}/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]


async def test_metrics_contains_expected_families_after_request(gateway_url, mock_server):
    """After a /v1/messages request, /metrics exposes all advertised metric families."""
    mock_server.enqueue(text_response("metrics test"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{gateway_url}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 100,
                "stream": False,
            },
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )
        assert resp.status_code == 200, f"Gateway returned {resp.status_code}: {resp.text[:500]}"

        metrics_resp = await client.get(f"{gateway_url}/metrics")
        assert metrics_resp.status_code == 200
        body = metrics_resp.text

        assert "luthien_requests_completed" in body, f"Missing luthien_requests_completed in:\n{body[:500]}"
        assert "luthien_tokens" in body, f"Missing luthien_tokens in:\n{body[:500]}"
        assert "luthien_request_ttfb_seconds" in body, f"Missing luthien_request_ttfb_seconds in:\n{body[:500]}"
        assert "luthien_active_requests" in body, f"Missing luthien_active_requests in:\n{body[:500]}"


async def test_metrics_no_double_suffixes(gateway_url, mock_server):
    """OTel Prometheus exporter should not mangle names with double suffixes.

    Catches _total_total or _seconds_seconds from unit-appending behavior.
    """
    mock_server.enqueue(text_response("suffix test"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            f"{gateway_url}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 100,
                "stream": False,
            },
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )

        metrics_resp = await client.get(f"{gateway_url}/metrics")
        body = metrics_resp.text

        assert "luthien_requests_completed_total_total" not in body, "Double _total suffix detected"
        assert "luthien_tokens_total_total" not in body, "Double _total suffix detected"
        assert "luthien_request_ttfb_seconds_seconds" not in body, "Double _seconds suffix detected"


async def test_metrics_no_auth_required(gateway_url):
    """GET /metrics should not require authentication (standard Prometheus convention)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{gateway_url}/metrics")
        assert resp.status_code == 200
