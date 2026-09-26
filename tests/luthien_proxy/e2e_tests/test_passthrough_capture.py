from __future__ import annotations

import asyncio
import os
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field

import httpx
import pytest
import uvicorn
from aiohttp import web

from luthien_proxy.main import create_app
from luthien_proxy.settings import clear_settings_cache
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations

pytestmark = pytest.mark.sqlite_e2e

_API_KEY = "test-passthrough-client-key"
_ADMIN_API_KEY = "test-passthrough-admin-key"
_GEMINI_STREAM_ENDPOINT = "/gemini/v1beta/models/gemini-2.5-pro:streamGenerateContent"
_ERROR_SECRETS = ("client-openai-secret", "client-query-secret", "upstream-url-secret")


@dataclass
class _ProviderServer:
    provider: str
    port: int = 0
    requests: list[dict[str, str]] = field(default_factory=list)
    bodies: list[dict[str, str]] = field(default_factory=list)
    _thread: threading.Thread | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    _runner: web.AppRunner | None = None

    def start(self) -> None:
        self.port = self.port or _free_port()
        ready = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            loop.run_until_complete(self._start_async())
            ready.set()
            loop.run_forever()
            loop.run_until_complete(self._stop_async())
            loop.close()

        self._thread = threading.Thread(target=run, daemon=True, name=f"mock-{self.provider}")
        self._thread.start()
        if not ready.wait(timeout=5):
            raise RuntimeError(f"mock {self.provider} did not start")

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    async def _start_async(self) -> None:
        app = web.Application(client_max_size=10 * 1024**2)
        app.router.add_route("*", "/{path:.*}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await site.start()

    async def _stop_async(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append({"path": request.path_qs, "authorization": request.headers.get("Authorization", "")})
        self.bodies.append(body)
        if self.provider == "openai":
            return web.json_response({"id": "resp-1", "model": body.get("model"), "output_text": "openai ok"})
        if "streamGenerateContent" in request.path and request.query.get("alt") == "sse":
            return web.Response(
                body=b'data: {"candidates":[{"content":{"parts":[{"text":"gem"}]}}]}\n\n',
                content_type="text/event-stream",
            )
        return web.json_response({"candidates": [{"content": {"parts": [{"text": "gemini ok"}]}}]})


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def mock_openai() -> Iterator[_ProviderServer]:
    server = _ProviderServer("openai")
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def mock_gemini() -> Iterator[_ProviderServer]:
    server = _ProviderServer("gemini")
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def passthrough_gateway(mock_openai: _ProviderServer, mock_gemini: _ProviderServer) -> Iterator[str]:
    with _boot_gateway(
        openai_url=f"http://127.0.0.1:{mock_openai.port}", gemini_url=f"http://127.0.0.1:{mock_gemini.port}"
    ) as url:
        yield url


@pytest.fixture
def passthrough_gateway_with_unreachable_openai(mock_gemini: _ProviderServer) -> Iterator[str]:
    with _boot_gateway(
        openai_url=f"http://127.0.0.1:{_free_port()}?key=upstream-url-secret",
        gemini_url=f"http://127.0.0.1:{mock_gemini.port}",
    ) as url:
        yield url


@pytest.fixture
def passthrough_gateway_disabled(mock_openai: _ProviderServer, mock_gemini: _ProviderServer) -> Iterator[str]:
    with _boot_gateway(
        openai_url=f"http://127.0.0.1:{mock_openai.port}",
        gemini_url=f"http://127.0.0.1:{mock_gemini.port}",
        passthrough_enabled=False,
    ) as url:
        yield url


@contextmanager
def _boot_gateway(*, openai_url: str, gemini_url: str, passthrough_enabled: bool = True) -> Iterator[str]:
    port = _free_port()
    with ExitStack() as stack:
        tmp_dir = tempfile.mkdtemp(prefix="luthien_passthrough_e2e_")
        stack.callback(shutil.rmtree, tmp_dir, ignore_errors=True)
        loop = asyncio.new_event_loop()
        stack.callback(loop.close)
        db_pool = DatabasePool(f"sqlite:///{os.path.join(tmp_dir, 'test.db')}")
        stack.callback(lambda: loop.run_until_complete(db_pool.close()))
        loop.run_until_complete(check_migrations(db_pool))
        env_keys = (
            "OPENAI_BASE_URL",
            "GEMINI_BASE_URL",
            "ANTHROPIC_API_KEY",
            "ENABLE_REQUEST_LOGGING",
            "PASSTHROUGH_ROUTES_ENABLED",
        )
        old_env = {key: os.environ.get(key) for key in env_keys}
        stack.callback(lambda: _restore_env(old_env))
        stack.callback(clear_settings_cache)
        os.environ["OPENAI_BASE_URL"] = openai_url
        os.environ["GEMINI_BASE_URL"] = gemini_url
        os.environ["ANTHROPIC_API_KEY"] = "mock-key"
        os.environ["ENABLE_REQUEST_LOGGING"] = "true"
        os.environ["PASSTHROUGH_ROUTES_ENABLED"] = "true" if passthrough_enabled else "false"
        clear_settings_cache()
        app = create_app(
            api_key=_API_KEY,
            admin_key=_ADMIN_API_KEY,
            db_pool=db_pool,
            redis_client=None,
            startup_policy_path="config/policy_config.yaml",
        )
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True, name="passthrough-sqlite-gateway")
        thread.start()
        stack.callback(lambda: _stop_uvicorn(server, thread))
        _wait_for_port(port)
        yield f"http://127.0.0.1:{port}"


def _restore_env(old_env: dict[str, str | None]) -> None:
    for key, value in old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _stop_uvicorn(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)


def _wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("passthrough gateway did not start")


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        yield client


async def _logs(client: httpx.AsyncClient, gateway_url: str, **params: str) -> list[dict[str, str]]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = await client.get(
            f"{gateway_url}/request-logs",
            headers={"Authorization": f"Bearer {_ADMIN_API_KEY}"},
            params=params,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        if data["logs"]:
            return data["logs"]
        await asyncio.sleep(0.1)
    raise AssertionError("request logs were not flushed")


@pytest.mark.asyncio
async def test_openai_passthrough_persists_full_request_and_response_bodies(
    passthrough_gateway: str,
    mock_openai: _ProviderServer,
) -> None:
    # Given
    large_text = "x" * (2 * 1024 * 1024)
    body = {"model": "gpt-4.1", "input": large_text}

    # When
    async with _client() as client:
        response = await client.post(
            f"{passthrough_gateway}/openai/v1/responses",
            json=body,
            headers={"Authorization": "Bearer client-openai-secret", "x-session-id": "session-openai"},
        )
        logs = await _logs(client, passthrough_gateway, endpoint="/openai/v1/responses", session_id="session-openai")

    # Then
    assert response.status_code == 200, response.text
    assert mock_openai.requests[-1]["authorization"] == "Bearer client-openai-secret"
    inbound = next(log for log in logs if log["direction"] == "inbound")
    assert inbound["request_body"] == body
    assert inbound["response_body"] == {"id": "resp-1", "model": "gpt-4.1", "output_text": "openai ok"}
    assert (inbound["session_id"], inbound["endpoint"]) == ("session-openai", "/openai/v1/responses")
    assert inbound["model"] == "gpt-4.1"
    assert "_truncated" not in str(inbound["request_body"])
    assert "client-openai-secret" not in f"{inbound}{next(log for log in logs if log['direction'] == 'outbound')}"


@pytest.mark.asyncio
async def test_gemini_passthrough_persists_session_model_and_streaming_wrapper(
    passthrough_gateway: str,
    mock_gemini: _ProviderServer,
) -> None:
    # Given
    body = {"contents": [{"parts": [{"text": "hello"}]}]}

    # When
    async with _client() as client:
        response = await client.post(
            f"{passthrough_gateway}/gemini/v1beta/models/gemini-2.5-pro:streamGenerateContent?alt=sse&key=client-google-secret",
            json=body,
            headers={"x-goog-api-key": "client-google-secret", "x-session-id": "session-gemini"},
        )
        await response.aread()
        logs = await _logs(
            client,
            passthrough_gateway,
            endpoint=_GEMINI_STREAM_ENDPOINT,
            session_id="session-gemini",
        )

    # Then
    assert (response.status_code, mock_gemini.requests[-1]["path"].endswith("key=client-google-secret")) == (200, True)
    inbound = next(log for log in logs if log["direction"] == "inbound")
    assert inbound["request_body"] == body
    assert (inbound["session_id"], inbound["endpoint"]) == ("session-gemini", _GEMINI_STREAM_ENDPOINT)
    assert inbound["model"] == "gemini-2.5-pro"
    assert inbound["response_body"] == {
        "stream_format": "gemini-sse",
        "chunks": [{"candidates": [{"content": {"parts": [{"text": "gem"}]}}]}],
        "final": None,
    }
    assert "client-google-secret" not in f"{inbound}{next(log for log in logs if log['direction'] == 'outbound')}"


@pytest.mark.asyncio
async def test_openai_passthrough_persists_request_body_and_error_when_upstream_unreachable(
    passthrough_gateway_with_unreachable_openai: str,
) -> None:
    # Given
    body = {"model": "gpt-4.1", "input": "capture even on connect failure"}

    # When
    async with _client() as client:
        response = await client.post(
            f"{passthrough_gateway_with_unreachable_openai}/openai/v1/responses?key=client-query-secret",
            json=body,
            headers={"Authorization": "Bearer client-openai-secret", "x-session-id": "session-error"},
        )
        logs = await _logs(
            client,
            passthrough_gateway_with_unreachable_openai,
            endpoint="/openai/v1/responses",
            session_id="session-error",
        )

    # Then
    assert response.status_code == 502
    inbound = next(log for log in logs if log["direction"] == "inbound")
    assert inbound["request_body"] == body
    assert inbound["response_status"] == 502
    assert inbound["error"] is not None
    assert all(secret not in str(inbound) for secret in _ERROR_SECRETS)


@pytest.mark.asyncio
async def test_passthrough_routes_404_when_feature_disabled(
    passthrough_gateway_disabled: str,
    mock_openai: _ProviderServer,
    mock_gemini: _ProviderServer,
) -> None:
    # Given the passthrough feature is disabled (the default)
    requests_before = (len(mock_openai.requests), len(mock_gemini.requests))

    # When both routes are hit
    async with _client() as client:
        openai_response = await client.post(
            f"{passthrough_gateway_disabled}/openai/v1/responses",
            json={"model": "gpt-4.1", "input": "should not reach upstream"},
            headers={"Authorization": "Bearer client-openai-secret"},
        )
        gemini_response = await client.post(
            f"{passthrough_gateway_disabled}/gemini/v1beta/models/gemini-2.5-pro:generateContent",
            json={"contents": [{"parts": [{"text": "hello"}]}]},
            headers={"x-goog-api-key": "client-google-secret"},
        )

    # Then both 404 and nothing was relayed upstream
    assert (openai_response.status_code, gemini_response.status_code) == (404, 404)
    assert (len(mock_openai.requests), len(mock_gemini.requests)) == requests_before
