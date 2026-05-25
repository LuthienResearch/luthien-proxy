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
_SESSION_ID = "test-session-fragment-01"
_CALL_ID = "call-fragment-01"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def gateway_url():
    tmp_dir = tempfile.mkdtemp(prefix="luthien_test_fragment_")
    db_path = os.path.join(tmp_dir, "test.db")
    db_url = f"sqlite:///{db_path}"

    loop = asyncio.new_event_loop()
    db_pool = db.DatabasePool(db_url)
    loop.run_until_complete(check_migrations(db_pool))

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO conversation_calls (call_id, model_name, provider, status, session_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (_CALL_ID, "claude-3", "anthropic", "completed", _SESSION_ID, "2025-01-15T10:00:00"),
    )
    for i in range(5):
        conn.execute(
            "INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"event-frag-0{i}",
                _CALL_ID,
                f"test.event.type{i}",
                json.dumps({"seq": i, "data": f"payload-{i}"}),
                _SESSION_ID,
                f"2025-01-15T10:00:0{i + 1}",
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
    thread = threading.Thread(target=server.run, daemon=True, name="fragment-test-gateway")
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
        raise RuntimeError("Fragment test gateway did not start")

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


async def test_fragment_turns_returns_html(gateway_url, auth_headers):
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp = await client.get(f"/ui/fragments/sessions/{_SESSION_ID}/turns")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "charset=utf-8" in resp.headers["content-type"]


async def test_fragment_turns_unauthenticated(gateway_url):
    async with httpx.AsyncClient(base_url=gateway_url, follow_redirects=False) as client:
        resp = await client.get(f"/ui/fragments/sessions/{_SESSION_ID}/turns")

    assert resp.status_code == 303


async def test_fragment_turns_bad_cursor_returns_400(gateway_url, auth_headers):
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp = await client.get(
            f"/ui/fragments/sessions/{_SESSION_ID}/turns",
            params={"cursor": "not-a-valid-cursor-garbage!!"},
        )

    assert resp.status_code == 400


async def test_fragment_turns_first_page(gateway_url, auth_headers):
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp = await client.get(
            f"/ui/fragments/sessions/{_SESSION_ID}/turns",
            params={"limit": 3},
        )

    assert resp.status_code == 200
    body = resp.text
    assert "turn-row" in body
    assert "test.event.type0" in body
    assert "load-more-sentinel" in body


async def test_fragment_turns_pagination(gateway_url, auth_headers):
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp1 = await client.get(
            f"/ui/fragments/sessions/{_SESSION_ID}/turns",
            params={"limit": 2},
        )

    assert resp1.status_code == 200
    assert "load-more-sentinel" in resp1.text

    match = re.search(r'data-cursor="([^"]+)"', resp1.text)
    assert match, "No cursor found in first page response"
    cursor = match.group(1)

    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp2 = await client.get(
            f"/ui/fragments/sessions/{_SESSION_ID}/turns",
            params={"limit": 2, "cursor": cursor},
        )

    assert resp2.status_code == 200
    assert "turn-row" in resp2.text
    assert resp2.text != resp1.text


async def test_existing_json_endpoint_unchanged(gateway_url, auth_headers):
    async with httpx.AsyncClient(base_url=gateway_url, headers=auth_headers) as client:
        resp = await client.get(f"/api/history/sessions/{_SESSION_ID}")

    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert "turns" in data
    assert "first_timestamp" in data
    assert "last_timestamp" in data
    assert "total_policy_interventions" in data
    assert "models_used" in data
    assert data["session_id"] == _SESSION_ID
