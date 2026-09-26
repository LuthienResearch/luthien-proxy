from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import anyio
import httpx
import pytest
from asgi_lifespan import LifespanManager
from pytest_httpx import HTTPXMock

from luthien_proxy.main import create_app
from luthien_proxy.request_log.recorder import RequestLogRecorder
from luthien_proxy.settings import clear_settings_cache
from luthien_proxy.utils.db import DatabasePool

pytestmark = pytest.mark.sqlite_e2e

_ADMIN_KEY = "passthrough-materialization-admin"
_SESSION_ID = "passthrough-materialization-session"
_USER_ID = "passthrough-materialization-user"
_OPENAI_URL = "https://openai.materialization.test/v1/chat/completions"
_GEMINI_URL = "https://gemini.materialization.test/v1beta/models/gemini-2.5-flash:generateContent"
_OPENAI_REQUEST = {
    "model": "gpt-4.1-mini",
    "messages": [{"role": "user", "content": "OpenAI materialize needle"}],
}
_OPENAI_RESPONSE = {
    "id": "chatcmpl-materialization",
    "model": "gpt-4.1-mini",
    "choices": [
        {
            "message": {"role": "assistant", "content": "OpenAI materialized answer"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14},
}
_GEMINI_REQUEST = {
    "contents": [{"role": "user", "parts": [{"text": "Gemini materialize needle"}]}],
}
_GEMINI_RESPONSE = {
    "responseId": "gemini-materialization",
    "modelVersion": "gemini-2.5-flash",
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": "Gemini materialized answer"}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 4, "totalTokenCount": 11},
}


@pytest.fixture
async def materialization_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used-by-passthrough")
    monkeypatch.setenv("ENABLE_REQUEST_LOGGING", "true")
    monkeypatch.setenv("GEMINI_BASE_URL", "https://gemini.materialization.test")
    monkeypatch.setenv("LOCALHOST_AUTH_BYPASS", "false")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.materialization.test")
    monkeypatch.setenv("PASSTHROUGH_MATERIALIZE_ENABLED", "true")
    monkeypatch.setenv("PASSTHROUGH_ROUTES_ENABLED", "true")
    monkeypatch.setenv("TRUST_USER_ID_HEADER", "true")
    monkeypatch.setenv("USAGE_TELEMETRY", "false")
    monkeypatch.setenv("WEBHOOK_URL", "")
    clear_settings_cache()

    pool = DatabasePool(f"sqlite:///{tmp_path / 'materialization.db'}")
    app = create_app(
        api_key="passthrough-materialization-client",
        admin_key=_ADMIN_KEY,
        db_pool=pool,
        redis_client=None,
        startup_policy_path="config/policy_config.yaml",
    )
    try:
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://luthien.test") as client:
                yield client
    finally:
        await pool.close()
        clear_settings_cache()


@pytest.fixture
def await_passthrough_flushes(monkeypatch: pytest.MonkeyPatch) -> Callable[[], Awaitable[None]]:
    writes_finished = 0
    flushes_finished = anyio.Event()
    original_write_logs = RequestLogRecorder._write_logs

    async def tracked_write_logs(recorder: RequestLogRecorder) -> None:
        nonlocal writes_finished
        await original_write_logs(recorder)
        writes_finished += 1
        if writes_finished == 2:
            flushes_finished.set()

    monkeypatch.setattr(RequestLogRecorder, "_write_logs", tracked_write_logs)

    async def wait_for_flushes() -> None:
        with anyio.fail_after(5):
            await flushes_finished.wait()
        assert writes_finished == 2

    return wait_for_flushes


async def test_passthrough_materializes_openai_and_gemini_read_surfaces(
    materialization_client: httpx.AsyncClient,
    await_passthrough_flushes: Callable[[], Awaitable[None]],
    httpx_mock: HTTPXMock,
) -> None:
    # Given
    httpx_mock.add_response(method="POST", url=_OPENAI_URL, json=_OPENAI_RESPONSE)
    httpx_mock.add_response(method="POST", url=_GEMINI_URL, json=_GEMINI_RESPONSE)
    passthrough_headers = {
        "Authorization": "Bearer passthrough-provider-token",
        "X-Luthien-User-Id": _USER_ID,
        "X-Session-Id": _SESSION_ID,
    }
    admin_headers = {"Authorization": f"Bearer {_ADMIN_KEY}"}

    # When
    openai_response = await materialization_client.post(
        "/openai/v1/chat/completions", headers=passthrough_headers, json=_OPENAI_REQUEST
    )
    gemini_response = await materialization_client.post(
        "/gemini/v1beta/models/gemini-2.5-flash:generateContent",
        headers=passthrough_headers,
        json=_GEMINI_REQUEST,
    )
    await await_passthrough_flushes()

    # Then
    assert openai_response.json() == _OPENAI_RESPONSE
    assert gemini_response.json() == _GEMINI_RESPONSE

    sessions = await materialization_client.get("/api/history/sessions", headers=admin_headers)
    assert sessions.status_code == 200
    session = next(item for item in sessions.json()["sessions"] if item["session_id"] == _SESSION_ID)
    assert set(session["models_used"]) == {"gpt-4.1-mini", "gemini-2.5-flash"}
    assert session["user_ids"] == [_USER_ID]

    detail = await materialization_client.get(f"/api/history/sessions/{_SESSION_ID}", headers=admin_headers)
    assert detail.status_code == 200
    turns = detail.json()["turns"]
    assert {turn["model"] for turn in turns} == {"gpt-4.1-mini", "gemini-2.5-flash"}
    assert {turn["request_messages"][0]["content"] for turn in turns} == {
        "OpenAI materialize needle",
        "Gemini materialize needle",
    }
    assert {turn["response_messages"][0]["content"] for turn in turns} == {
        "OpenAI materialized answer",
        "Gemini materialized answer",
    }

    export = await materialization_client.get(
        f"/api/history/sessions/{_SESSION_ID}/export/jsonl", headers=admin_headers
    )
    assert export.status_code == 200
    exported_turns = [json.loads(line) for line in export.text.splitlines() if line]
    call_ids = {turn["call_id"] for turn in turns}
    assert {turn["call_id"] for turn in exported_turns} == call_ids

    debug_payloads = {}
    for call_id in call_ids:
        debug = await materialization_client.get(f"/api/debug/calls/{call_id}", headers=admin_headers)
        assert debug.status_code == 200
        events = debug.json()["events"]
        request_payload = next(
            event["payload"] for event in events if event["event_type"] == "transaction.request_recorded"
        )
        response_payload = next(
            event["payload"] for event in events if event["event_type"] == "transaction.non_streaming_response_recorded"
        )
        debug_payloads[request_payload["provider"]] = {"request": request_payload, "response": response_payload}

    assert set(debug_payloads) == {"openai", "gemini"}
    assert debug_payloads["openai"]["request"]["provider_request"] == _OPENAI_REQUEST
    assert debug_payloads["openai"]["request"]["final_request"]["model"] == "gpt-4.1-mini"
    assert debug_payloads["openai"]["response"]["provider_response"] == _OPENAI_RESPONSE
    assert debug_payloads["openai"]["response"]["final_response"]["usage"] == {
        "input_tokens": 9,
        "output_tokens": 5,
        "total_tokens": 14,
    }
    assert debug_payloads["gemini"]["request"]["provider_request"] == _GEMINI_REQUEST
    assert debug_payloads["gemini"]["request"]["final_request"]["model"] == "gemini-2.5-flash"
    assert debug_payloads["gemini"]["response"]["provider_response"] == _GEMINI_RESPONSE
    assert debug_payloads["gemini"]["response"]["final_response"]["usage"] == {
        "input_tokens": 7,
        "output_tokens": 4,
        "total_tokens": 11,
    }

    fts = await materialization_client.get(
        "/api/history/sessions", headers=admin_headers, params={"q": "OpenAI materialize needle"}
    )
    assert fts.status_code == 200
    assert [item["session_id"] for item in fts.json()["sessions"]] == [_SESSION_ID]
