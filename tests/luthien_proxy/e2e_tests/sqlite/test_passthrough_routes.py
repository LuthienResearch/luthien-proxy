import asyncio

import aiosqlite
import httpx
import pytest

pytestmark = pytest.mark.sqlite_e2e


@pytest.mark.asyncio
async def test_openai_headers_persist_to_request_logs(sqlite_gateway_url, sqlite_db_path, api_key, mock_openai_server):
    mock_openai_server.clear_requests()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{sqlite_gateway_url}/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "x-luthien-session-id": "persist-test-session",
                "x-luthien-agent": "build",
                "x-luthien-model": "gpt-4o",
            },
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200

    await asyncio.sleep(0.5)

    async with aiosqlite.connect(sqlite_db_path) as db:
        cursor = await db.execute(
            "SELECT session_id, agent, model, endpoint FROM request_logs"
            " WHERE direction='inbound' AND session_id='persist-test-session'"
            " ORDER BY created_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()

    assert row is not None, "No log row found — ENABLE_REQUEST_LOGGING not set or INSERT failed"
    assert row[0] == "persist-test-session"
    assert row[1] == "build"
    assert row[2] == "gpt-4o"
    assert "/openai/" in row[3]


@pytest.mark.asyncio
async def test_openai_missing_luthien_headers_null_columns(
    sqlite_gateway_url, sqlite_db_path, api_key, mock_openai_server
):
    mock_openai_server.clear_requests()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{sqlite_gateway_url}/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "x-luthien-model": "null-test-marker",
            },
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200

    await asyncio.sleep(0.5)

    async with aiosqlite.connect(sqlite_db_path) as db:
        cursor = await db.execute(
            "SELECT session_id, agent FROM request_logs"
            " WHERE direction='inbound' AND model='null-test-marker'"
            " ORDER BY created_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()

    assert row is not None, "No log row found"
    assert row[0] is None, "session_id should be NULL when x-luthien-session-id is absent"
    assert row[1] is None, "agent should be NULL when x-luthien-agent is absent"


def test_openai_missing_auth_returns_401(sqlite_gateway_url):
    response = httpx.post(
        f"{sqlite_gateway_url}/openai/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10,
    )
    assert response.status_code == 401


def test_openai_server_key_not_leaked_to_upstream(sqlite_gateway_url, api_key, mock_openai_server):
    mock_openai_server.clear_requests()
    httpx.post(
        f"{sqlite_gateway_url}/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10,
    )
    captured = mock_openai_server.last_request_headers()
    assert captured is not None
    outbound_auth = captured.get("Authorization", "")
    assert api_key not in outbound_auth, "Proxy key must not reach upstream"


def test_gemini_api_key_injected(sqlite_gateway_url, api_key, mock_gemini_server):
    mock_gemini_server.clear_requests()
    httpx.post(
        f"{sqlite_gateway_url}/gemini/v1beta/models/gemini-1.5-flash:generateContent",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"contents": [{"parts": [{"text": "hi"}]}]},
        timeout=10,
    )
    captured = mock_gemini_server.last_request_headers()
    assert captured is not None
    lower_keys = {k.lower() for k in captured}
    assert "x-goog-api-key" in lower_keys, "Google API key header not injected"


def test_luthien_headers_stripped_from_outbound(sqlite_gateway_url, api_key, mock_openai_server):
    mock_openai_server.clear_requests()
    httpx.post(
        f"{sqlite_gateway_url}/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "x-luthien-session-id": "strip-test-session",
            "x-luthien-agent": "build",
            "x-luthien-model": "gpt-4o",
            "x-luthien-provider": "openai",
        },
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10,
    )
    captured = mock_openai_server.last_request_headers()
    assert captured is not None
    leaked = [k for k in captured if k.lower().startswith("x-luthien-")]
    assert len(leaked) == 0, f"x-luthien-* headers leaked to upstream: {leaked}"


def test_anthropic_alias_route_is_registered(sqlite_gateway_url):
    response = httpx.get(
        f"{sqlite_gateway_url}/anthropic/v1/models",
        timeout=5.0,
    )
    assert response.status_code == 401
