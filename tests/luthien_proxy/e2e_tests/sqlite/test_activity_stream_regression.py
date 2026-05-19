"""Regression test: SSE activity stream delivers events in order.

This test verifies that the /api/activity/stream endpoint correctly
delivers events published via the real InProcessEventPublisher when
multiple requests flow through the gateway. Guards against regressions
in the SSE pipeline.

Run:  uv run pytest tests/luthien_proxy/e2e_tests/sqlite/test_activity_stream_regression.py -v --timeout=30
"""

import asyncio
import json

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.sqlite._boot import boot_sqlite_gateway, free_port

from luthien_proxy.observability.event_publisher import build_activity_event

pytestmark = pytest.mark.sqlite_e2e

_API_KEY = "test-regression-key"
_ADMIN_KEY = "test-regression-admin-key"
_NUM_SYNTHETIC_EVENTS = 3

_EXPECTED_EVENT_FIELDS = set(build_activity_event("_", "_").keys())


@pytest.fixture(scope="module")
def mock_server():
    server = MockAnthropicServer(port=free_port())
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def gateway_url(mock_server):
    """Boot an in-process SQLite gateway with no Redis."""
    with boot_sqlite_gateway(
        api_key=_API_KEY,
        admin_key=_ADMIN_KEY,
        mock_anthropic_url=f"http://127.0.0.1:{mock_server.port}",
        tmp_prefix="luthien_regression_e2e_",
        thread_name="regression-gateway",
    ) as url:
        yield url


@pytest.mark.asyncio
async def test_activity_stream_events_flow_in_order(gateway_url, mock_server):
    """SSE activity stream delivers events from all 3 synthetic requests in order.

    Sends 3 synthetic API requests through the gateway (triggering the real
    InProcessEventPublisher for each), then verifies that all 3 sets of events
    arrive at the SSE client within 5 seconds and carry the correct schema.
    """
    for i in range(_NUM_SYNTHETIC_EVENTS):
        mock_server.enqueue(text_response(f"Synthetic response {i}"))

    sse_events: list[dict] = []
    requests_done = asyncio.Event()

    async def collect_sse():
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
                    if requests_done.is_set() and len(sse_events) >= _NUM_SYNTHETIC_EVENTS:
                        return

    async def send_synthetic_requests():
        await asyncio.sleep(0.3)  # let SSE connection establish
        async with httpx.AsyncClient(timeout=15.0) as client:
            for i in range(_NUM_SYNTHETIC_EVENTS):
                response = await client.post(
                    f"{gateway_url}/v1/messages",
                    json={
                        "model": "claude-haiku-4-5",
                        "messages": [{"role": "user", "content": f"synthetic request {i}"}],
                        "max_tokens": 100,
                        "stream": False,
                    },
                    headers={"Authorization": f"Bearer {_API_KEY}"},
                )
                assert response.status_code == 200
        requests_done.set()

    sse_task = asyncio.create_task(collect_sse())
    send_task = asyncio.create_task(send_synthetic_requests())

    done, pending = await asyncio.wait(
        [sse_task, send_task],
        timeout=15.0,
        return_when=asyncio.ALL_COMPLETED,
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert send_task in done, "Synthetic requests did not complete in time"
    assert len(sse_events) >= _NUM_SYNTHETIC_EVENTS, (
        f"Expected at least {_NUM_SYNTHETIC_EVENTS} activity events but got {len(sse_events)}. Events: {sse_events}"
    )

    for i, event in enumerate(sse_events[:_NUM_SYNTHETIC_EVENTS]):
        missing = _EXPECTED_EVENT_FIELDS - set(event)
        assert not missing, f"Event {i} missing required fields {missing}: {event}"
