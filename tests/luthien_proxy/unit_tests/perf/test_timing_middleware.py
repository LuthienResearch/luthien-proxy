from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from luthien_proxy.perf.timing_middleware import (
    ServerTimingMiddleware,
    format_phases,
    time_phase,
)


def _make_app(path: str = "/api/history/sessions") -> FastAPI:
    app = FastAPI()
    app.add_middleware(ServerTimingMiddleware)

    @app.get(path)
    async def endpoint():
        with time_phase("handler"):
            pass
        return {"ok": True}

    return app


@pytest.fixture
def history_app():
    return _make_app("/api/history/sessions")


@pytest.fixture
def v1_app():
    return _make_app("/v1/messages")


def test_format_phases():
    result = format_phases([("db", 12.3), ("serialize", 4.5)])
    assert result == "db;dur=12.3, serialize;dur=4.5"


def test_format_phases_single():
    result = format_phases([("render", 1.0)])
    assert result == "render;dur=1.0"


def test_format_phases_empty():
    assert format_phases([]) == ""


@pytest.mark.asyncio
async def test_path_filter_includes_history(history_app):
    transport = ASGITransport(app=history_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/history/sessions")
    assert response.status_code == 200
    assert "Server-Timing" in response.headers


@pytest.mark.asyncio
async def test_path_filter_includes_debug():
    app = _make_app("/api/debug/calls")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/debug/calls")
    assert "Server-Timing" in response.headers


@pytest.mark.asyncio
async def test_path_filter_includes_ui_fragments():
    app = _make_app("/ui/fragments/sidebar")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ui/fragments/sidebar")
    assert "Server-Timing" in response.headers


@pytest.mark.asyncio
async def test_path_filter_excludes_v1_messages(v1_app):
    transport = ASGITransport(app=v1_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/messages")
    assert response.status_code == 200
    assert "Server-Timing" not in response.headers


@pytest.mark.asyncio
async def test_concurrent_isolation():
    barrier = asyncio.Event()
    results: dict[str, str | None] = {}

    app = FastAPI()
    app.add_middleware(ServerTimingMiddleware)

    @app.get("/api/history/a")
    async def endpoint_a():
        with time_phase("phase-a"):
            await barrier.wait()
        return {"id": "a"}

    @app.get("/api/history/b")
    async def endpoint_b():
        with time_phase("phase-b"):
            await asyncio.sleep(0)
        barrier.set()
        return {"id": "b"}

    transport = ASGITransport(app=app)

    async def call_a():
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/history/a")
        results["a"] = r.headers.get("Server-Timing")

    async def call_b():
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/history/b")
        results["b"] = r.headers.get("Server-Timing")

    await asyncio.gather(call_a(), call_b())

    header_a = results["a"]
    header_b = results["b"]

    assert header_a is not None
    assert header_b is not None
    assert "phase-b" not in header_a, f"phase-b leaked into request-a header: {header_a}"
    assert "phase-a" not in header_b, f"phase-a leaked into request-b header: {header_b}"
    assert "phase-a" in header_a
    assert "phase-b" in header_b


@pytest.mark.asyncio
async def test_monitored_path_with_zero_phases_omits_header():
    app = FastAPI()
    app.add_middleware(ServerTimingMiddleware)

    @app.get("/api/history/sessions")
    async def endpoint():
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/history/sessions")
    assert response.status_code == 200
    assert "Server-Timing" not in response.headers


def test_time_phase_outside_request_context_does_not_raise():
    with time_phase("orphan"):
        pass


@pytest.mark.asyncio
async def test_time_phase_records_elapsed_even_when_block_raises():
    from luthien_proxy.perf.timing_middleware import _phases_var

    phases: list[tuple[str, float]] = []
    token = _phases_var.set(phases)
    try:
        with pytest.raises(ValueError):
            with time_phase("failing-phase"):
                raise ValueError("boom")
    finally:
        _phases_var.reset(token)

    assert len(phases) == 1
    name, elapsed_ms = phases[0]
    assert name == "failing-phase"
    assert elapsed_ms >= 0


@pytest.mark.asyncio
async def test_static_cache_middleware_replaces_not_appends():
    from fastapi.testclient import TestClient
    from starlette.responses import Response as StarletteResponse

    from luthien_proxy.main import create_app
    from luthien_proxy.utils.db import DatabasePool

    db_pool = DatabasePool("sqlite:///:memory:")
    app = create_app(
        api_key=None,
        admin_key="test",
        db_pool=db_pool,
        redis_client=None,
        startup_policy_path=None,
        policy_source="file",
    )

    @app.get("/api/test-cache")
    def _route():
        return StarletteResponse(
            content="ok",
            headers={"Cache-Control": "no-cache"},
        )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/test-cache")
    await db_pool.close()

    cache_headers = [v for k, v in response.headers.items() if k.lower() == "cache-control"]
    assert len(cache_headers) == 1, f"Expected exactly one Cache-Control header, got: {cache_headers}"
