"""Test that activity stream works with in-process publisher (no Redis).

Boots a SQLite-backed gateway with redis_client=None and verifies that:
1. The /api/activity/stream SSE endpoint returns events
2. Events are delivered when a request flows through the gateway

Run:  uv run pytest tests/luthien_proxy/e2e_tests/sqlite/test_activity_stream.py -v --timeout=30
"""

import asyncio
import json
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

_API_KEY = "test-activity-key"
_ADMIN_KEY = "test-activity-admin-key"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_server():
    mock_port = _free_port()
    server = MockAnthropicServer(port=mock_port)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def gateway_url(mock_server):
    """Boot an in-process SQLite gateway with no Redis."""
    port = _free_port()
    tmp_dir = tempfile.mkdtemp(prefix="luthien_activity_e2e_")
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

    # Flush cached settings so create_app() picks up the env vars set above.
    # Without this, get_settings() may return a stale instance that lacks
    # ANTHROPIC_API_KEY, causing credential resolution to fail. Module-scoped
    # fixtures run before function-scoped autouse fixtures can clear the cache.
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
    thread = threading.Thread(target=server.run, daemon=True, name="activity-gateway")
    thread.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Gateway did not start")

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
    clear_settings_cache()


@pytest.mark.asyncio
async def test_activity_stream_receives_events(gateway_url, mock_server):
    """SSE activity stream delivers events when a request is processed.

    This validates that InProcessEventPublisher works end-to-end:
    1. Connect to /api/activity/stream as SSE client
    2. Send a request through the gateway
    3. Verify the SSE client receives activity events
    """
    mock_server.enqueue(text_response("Hello from activity test"))

    sse_events: list[dict] = []
    request_done = asyncio.Event()

    async def collect_sse():
        """Connect to SSE stream and collect events until we have enough or timeout."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            async with client.stream(
                "GET",
                f"{gateway_url}/api/activity/stream",
                headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].strip()
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    sse_events.append(event)
                    # Once we have events and the request is done, stop collecting
                    if request_done.is_set() and len(sse_events) >= 1:
                        return

    async def send_request():
        """Send a request through the gateway after a brief delay."""
        await asyncio.sleep(0.3)  # let SSE connection establish
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={
                    "model": "claude-haiku-4-5",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 100,
                    "stream": False,
                },
                headers={"Authorization": f"Bearer {_API_KEY}"},
            )
            assert response.status_code == 200
        request_done.set()

    # Run SSE collection and request sending concurrently
    sse_task = asyncio.create_task(collect_sse())
    send_task = asyncio.create_task(send_request())

    # Wait for both, with a generous timeout
    done, pending = await asyncio.wait(
        [sse_task, send_task],
        timeout=10.0,
        return_when=asyncio.ALL_COMPLETED,
    )

    # Cancel anything still pending
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # The request must have succeeded
    assert send_task in done, "Request did not complete in time"

    # We should have received at least one activity event via SSE
    assert len(sse_events) >= 1, f"Expected activity events but got none. SSE events: {sse_events}"

    # Verify event structure
    event = sse_events[0]
    assert "call_id" in event, f"Event missing call_id: {event}"
    assert "event_type" in event, f"Event missing event_type: {event}"
    assert "timestamp" in event, f"Event missing timestamp: {event}"


@pytest.mark.asyncio
async def test_activity_stream_returns_200_without_redis(gateway_url):
    """The /api/activity/stream endpoint returns 200 (not 503) when Redis is absent.

    Previously, this endpoint returned 503 when Redis was not configured.
    With the in-process publisher, it should always be available.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        async with client.stream(
            "GET",
            f"{gateway_url}/api/activity/stream",
            headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
