from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import sqlite3
import tempfile
import threading
import time

import httpx
import pytest
import uvicorn

from luthien_proxy.main import create_app
from luthien_proxy.settings import clear_settings_cache
from luthien_proxy.utils import db
from luthien_proxy.utils.migration_check import check_migrations

pytestmark = pytest.mark.integration

_ADMIN_KEY = "test-admin-key"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def gateway_url():
    tmp_dir = tempfile.mkdtemp(prefix="luthien_test_frag_sessions_")
    db_path = os.path.join(tmp_dir, "test.db")
    db_url = f"sqlite:///{db_path}"

    loop = asyncio.new_event_loop()
    db_pool = db.DatabasePool(db_url)
    loop.run_until_complete(check_migrations(db_pool))

    conn = sqlite3.connect(db_path)
    sessions = [
        ("sess-alpha", "call-alpha", "2025-01-15T10:00:00"),
        ("sess-beta", "call-beta", "2025-01-15T11:00:00"),
        ("sess-gamma", "call-gamma", "2025-01-15T12:00:00"),
    ]
    for sid, cid, ts in sessions:
        conn.execute(
            "INSERT INTO conversation_calls (call_id, model_name, provider, status, session_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (cid, "claude-3", "anthropic", "completed", sid, ts),
        )
        conn.execute(
            "INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"evt-{sid}",
                cid,
                "transaction.request_recorded",
                json.dumps(
                    {
                        "final_request": {
                            "messages": [{"role": "user", "content": f"Hello from {sid}"}],
                            "max_tokens": 100,
                        }
                    }
                ),
                sid,
                ts,
            ),
        )
    conn.commit()
    conn.close()

    old_env: dict[str, str | None] = {k: os.environ.get(k) for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY")}
    os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:1"
    os.environ["ANTHROPIC_API_KEY"] = "mock-key"
    clear_settings_cache()

    app = create_app(
        api_key=None,
        admin_key=_ADMIN_KEY,
        db_pool=db_pool,
        redis_client=None,
        startup_policy_path="config/policy_config.yaml",
        policy_source="db-fallback-file",
    )

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="fragment-sessions-test-gateway")
    thread.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("Fragment sessions test gateway did not start")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)

    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    clear_settings_cache()

    loop.run_until_complete(db_pool.close())
    loop.close()
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def auth_headers():
    return {"Authorization": f"Bearer {_ADMIN_KEY}"}


async def test_fragment_sessions_returns_html(gateway_url, auth_headers):
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp = await client.get("/ui/fragments/sessions")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "charset=utf-8" in resp.headers["content-type"]
    assert "sessions-fragment" in resp.text


async def test_fragment_sessions_unauthenticated(gateway_url):
    async with httpx.AsyncClient(base_url=gateway_url, follow_redirects=False) as client:
        resp = await client.get("/ui/fragments/sessions")

    assert resp.status_code in (200, 303, 401, 403)


async def test_fragment_sessions_bad_cursor_returns_400(gateway_url, auth_headers):
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp = await client.get(
            "/ui/fragments/sessions",
            params={"cursor": "not-a-valid-cursor-garbage!!"},
        )

    assert resp.status_code == 400


async def test_fragment_sessions_pagination(gateway_url, auth_headers):
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp1 = await client.get("/ui/fragments/sessions", params={"limit": 2})

    assert resp1.status_code == 200
    assert "session-card" in resp1.text
    assert "load-more-sentinel" in resp1.text

    match = re.search(r'data-cursor="([^"]+)"', resp1.text)
    assert match, "No cursor sentinel found in first-page response"
    cursor = match.group(1)

    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp2 = await client.get("/ui/fragments/sessions", params={"limit": 2, "cursor": cursor})

    assert resp2.status_code == 200
    assert "session-card" in resp2.text
    assert resp2.text != resp1.text


async def test_existing_json_endpoint_unchanged(gateway_url, auth_headers):
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp = await client.get("/api/history/sessions")

    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    data = resp.json()
    assert "sessions" in data
    assert "total" in data
    assert "offset" in data
    assert "has_more" in data


async def test_filter_q(gateway_url, auth_headers):
    """Filter by q param returns matching sessions."""
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp = await client.get(
            "/ui/fragments/sessions?q=alpha",
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert "session-card" in resp.text
    assert "sess-alpha" in resp.text
    assert "sess-beta" not in resp.text


async def test_filter_q_with_cursor(gateway_url, auth_headers):
    """Filter + cursor combination returns non-overlapping pages."""
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp1 = await client.get("/ui/fragments/sessions", params={"q": "sess", "limit": 2})

    assert resp1.status_code == 200
    assert "session-card" in resp1.text
    assert "load-more-sentinel" in resp1.text

    match = re.search(r'data-cursor="([^"]+)"', resp1.text)
    assert match, "Expected cursor sentinel on first page"
    cursor = match.group(1)

    ids1 = set(re.findall(r'data-session-id="([^"]+)"', resp1.text))
    assert len(ids1) == 2

    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp2 = await client.get("/ui/fragments/sessions", params={"q": "sess", "limit": 2, "cursor": cursor})

    assert resp2.status_code == 200
    assert "session-card" in resp2.text

    ids2 = set(re.findall(r'data-session-id="([^"]+)"', resp2.text))
    assert ids1.isdisjoint(ids2), "Page 2 returned sessions already shown on page 1"
