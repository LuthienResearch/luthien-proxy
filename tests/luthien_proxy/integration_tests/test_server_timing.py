"""Integration tests for ServerTimingMiddleware.

Tests that the Server-Timing header is correctly added to admin/debug/UI paths
and absent from gateway paths like /v1/messages.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.main import create_app
from luthien_proxy.utils import db

pytestmark = pytest.mark.integration


@pytest.fixture
def app_with_db():
    """Create an in-process app with SQLite for testing."""
    db_pool = db.DatabasePool("sqlite:///:memory:")

    app = create_app(
        api_key=None,
        admin_key="test-admin-key",
        db_pool=db_pool,
        redis_client=None,
        startup_policy_path=None,
        policy_source="file",
    )

    yield app

    asyncio.run(db_pool.close())


def test_server_timing_header_absent_on_v1_messages(app_with_db):
    """Server-Timing header should NOT be present on /v1/messages."""
    client = TestClient(app_with_db)
    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "test"}],
        },
    )
    assert "Server-Timing" not in response.headers


def test_server_timing_header_absent_on_health(app_with_db):
    """Server-Timing header should NOT be present on /health."""
    client = TestClient(app_with_db)
    response = client.get("/health")
    assert response.status_code == 200
    assert "Server-Timing" not in response.headers


def test_server_timing_header_present_on_timed_path():
    """Server-Timing header should be present on paths matching the timed prefixes."""
    from fastapi import FastAPI

    from luthien_proxy.perf.timing_middleware import ServerTimingMiddleware, time_phase

    mini_app = FastAPI()
    mini_app.add_middleware(ServerTimingMiddleware)

    @mini_app.get("/api/history/sessions")
    def sessions():
        with time_phase("handler"):
            pass
        return {}

    client = TestClient(mini_app)
    response = client.get("/api/history/sessions")
    assert response.status_code == 200
    assert "Server-Timing" in response.headers
    assert "handler" in response.headers["Server-Timing"]
