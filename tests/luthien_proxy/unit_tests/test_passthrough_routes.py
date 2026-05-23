from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from luthien_proxy.passthrough_auth import verify_passthrough_token, verify_strict_client_key
from luthien_proxy.passthrough_routes import router
from luthien_proxy.request_log.recorder import NoOpRequestLogRecorder, RequestLogRecorder
from luthien_proxy.utils.db import DatabasePool


@pytest.fixture(autouse=True)
def _patch_provider_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")


def _make_buffered_client(status_code: int = 200, content: bytes = b"{}", headers: dict | None = None) -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.content = content
    mock_response.headers = headers or {}
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_response)
    return mock_client


def _make_app(deps=None, buffered_client=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_passthrough_token] = lambda: "tok"
    app.dependency_overrides[verify_strict_client_key] = lambda: "tok"
    if deps is not None:
        app.state.dependencies = deps
    app.state.passthrough_buffered_client = buffered_client or _make_buffered_client()
    app.state.passthrough_streaming_client = MagicMock()
    return app


def _make_deps(*, enable_request_logging: bool = True) -> MagicMock:
    deps = MagicMock()
    deps.db_pool = MagicMock(spec=DatabasePool)
    deps.enable_request_logging = enable_request_logging
    return deps


class TestPassthroughRecorderCreation:
    def test_creates_noop_recorder_when_logging_disabled(self) -> None:
        deps = _make_deps(enable_request_logging=False)
        app = _make_app(deps)

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_recorder = MagicMock(spec=NoOpRequestLogRecorder)
            mock_create.return_value = mock_recorder

            client = TestClient(app, raise_server_exceptions=True)
            client.post("/openai/v1/chat/completions", json={"model": "gpt-4o", "messages": []})

            mock_create.assert_called_once()
            assert mock_create.call_args.kwargs["enabled"] is False

    def test_creates_noop_recorder_when_no_deps(self) -> None:
        app = _make_app(deps=None)

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_recorder = MagicMock(spec=NoOpRequestLogRecorder)
            mock_create.return_value = mock_recorder

            client = TestClient(app, raise_server_exceptions=True)
            client.post("/openai/v1/chat/completions", json={"model": "gpt-4o", "messages": []})

            mock_create.assert_called_once()
            assert mock_create.call_args.kwargs["enabled"] is False
            assert mock_create.call_args.kwargs["db_pool"] is None


class TestPassthroughLuthienHeadersPersisted:
    def _make_request_and_capture(self, headers: dict[str, str], body: dict) -> tuple[MagicMock, dict]:
        deps = _make_deps(enable_request_logging=True)
        app = _make_app(deps)

        captured: dict = {}

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_recorder = MagicMock(spec=RequestLogRecorder)
            mock_create.return_value = mock_recorder

            def capture_inbound(**kwargs):
                captured.update(kwargs)

            mock_recorder.record_inbound_request.side_effect = capture_inbound

            client = TestClient(app, raise_server_exceptions=True)
            client.post("/openai/v1/chat/completions", json=body, headers=headers)

        return mock_recorder, captured

    def test_session_id_passed_to_recorder(self) -> None:
        _, captured = self._make_request_and_capture(
            headers={"x-luthien-session-id": "sess-abc"},
            body={"model": "gpt-4o", "messages": []},
        )
        assert captured["session_id"] == "sess-abc"

    def test_agent_passed_to_recorder(self) -> None:
        _, captured = self._make_request_and_capture(
            headers={"x-luthien-agent": "opencode/1.0"},
            body={"model": "gpt-4o", "messages": []},
        )
        assert captured["agent"] == "opencode/1.0"

    def test_model_passed_to_recorder(self) -> None:
        _, captured = self._make_request_and_capture(
            headers={"x-luthien-model": "gpt-4o"},
            body={"model": "gpt-4o", "messages": []},
        )
        assert captured["model"] == "gpt-4o"

    def test_missing_luthien_headers_yields_none_values(self) -> None:
        _, captured = self._make_request_and_capture(
            headers={},
            body={"model": "gpt-4o", "messages": []},
        )
        assert captured["session_id"] is None
        assert captured["agent"] is None
        assert captured["model"] is None

    def test_all_luthien_headers_passed_together(self) -> None:
        _, captured = self._make_request_and_capture(
            headers={
                "x-luthien-session-id": "sess-xyz",
                "x-luthien-agent": "opencode/2.0",
                "x-luthien-model": "gpt-4o-mini",
            },
            body={"model": "gpt-4o-mini", "messages": []},
        )
        assert captured["session_id"] == "sess-xyz"
        assert captured["agent"] == "opencode/2.0"
        assert captured["model"] == "gpt-4o-mini"


class TestPassthroughRecorderFlush:
    def test_flush_called_after_buffered_response(self) -> None:
        deps = _make_deps(enable_request_logging=True)
        mock_buffered = _make_buffered_client(status_code=201, content=b'{"id": "chatcmpl-123"}')
        app = _make_app(deps, buffered_client=mock_buffered)

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_recorder = MagicMock(spec=RequestLogRecorder)
            mock_create.return_value = mock_recorder

            client = TestClient(app, raise_server_exceptions=True)
            client.post("/openai/v1/chat/completions", json={"model": "gpt-4o", "messages": []})

        mock_recorder.record_inbound_response.assert_called_once_with(status=201)
        mock_recorder.flush.assert_called_once()

    def test_flush_called_on_upstream_error(self) -> None:
        import httpx

        deps = _make_deps(enable_request_logging=True)
        mock_buffered = MagicMock()
        mock_buffered.request = AsyncMock(side_effect=httpx.ConnectError("refused"))
        app = _make_app(deps, buffered_client=mock_buffered)

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_recorder = MagicMock(spec=RequestLogRecorder)
            mock_create.return_value = mock_recorder

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/anthropic/v1/messages",
                json={"model": "claude-haiku-4-5", "max_tokens": 10, "messages": []},
                headers={"anthropic-version": "2023-06-01"},
            )

        assert response.status_code == 502
        mock_recorder.record_inbound_response.assert_called_once()
        args = mock_recorder.record_inbound_response.call_args.kwargs
        assert args["status"] == 502
        mock_recorder.flush.assert_called_once()


@pytest.mark.parametrize(
    "sensitive_header",
    [
        "authorization",
        "x-api-key",
        "x-anthropic-api-key",
        "x-goog-api-key",
    ],
)
def test_sensitive_headers_are_passed_to_recorder_raw(sensitive_header: str) -> None:
    deps = _make_deps(enable_request_logging=True)
    app = _make_app(deps)
    captured_headers: dict[str, str] = {}

    with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
        mock_recorder = MagicMock(spec=RequestLogRecorder)
        mock_create.return_value = mock_recorder

        def capture(**kwargs):
            captured_headers.update(kwargs.get("headers", {}))

        mock_recorder.record_inbound_request.side_effect = capture

        client = TestClient(app)
        client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers={sensitive_header: "secret-value"},
        )

    assert sensitive_header in captured_headers


@pytest.fixture
def policy_config_file():
    config_content = """
policy:
  class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
  config: {}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        config_path = f.name

    yield config_path
    Path(config_path).unlink(missing_ok=True)


@pytest.fixture
def mock_db_pool():
    mock = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock.get_pool = AsyncMock(return_value=mock_pool)
    mock.close = AsyncMock()
    mock.is_sqlite = False
    return mock


class TestBodySizeLimit:
    def test_body_size_limit_413(self) -> None:
        app = _make_app()

        with patch("luthien_proxy.passthrough_routes.MAX_REQUEST_PAYLOAD_BYTES", 5):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/openai/v1/chat/completions",
                content=b"123456",
                headers={"content-type": "application/octet-stream"},
            )

        assert response.status_code == 413

    def test_body_size_limit_normal_passes(self) -> None:
        app = _make_app()

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_create.return_value = MagicMock()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/openai/v1/chat/completions",
                json={"model": "gpt-4o", "messages": []},
            )

        assert response.status_code != 413


class TestHopByHopHeaderStripping:
    def test_hop_by_hop_stripped(self) -> None:
        upstream_headers = {
            "transfer-encoding": "chunked",
            "set-cookie": "x=y",
            "server": "nginx",
            "content-type": "application/json",
        }
        mock_buffered = _make_buffered_client(headers=upstream_headers)
        app = _make_app(buffered_client=mock_buffered)

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_create.return_value = MagicMock()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/openai/v1/chat/completions", json={"model": "gpt-4o", "messages": []})

        assert "transfer-encoding" not in response.headers
        assert "set-cookie" not in response.headers
        assert "server" not in response.headers

    def test_essential_headers_preserved(self) -> None:
        upstream_headers = {
            "content-type": "application/json",
            "x-request-id": "req-123",
        }
        mock_buffered = _make_buffered_client(headers=upstream_headers)
        app = _make_app(buffered_client=mock_buffered)

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_create.return_value = MagicMock()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/openai/v1/chat/completions", json={"model": "gpt-4o", "messages": []})

        assert response.headers.get("content-type", "").startswith("application/json")
        assert response.headers.get("x-request-id") == "req-123"


class TestLifespanHttpxClients:
    def test_lifespan_closes_httpx_clients(self, policy_config_file, mock_db_pool) -> None:
        from luthien_proxy.main import create_app as main_create_app

        app = main_create_app(
            api_key=None,
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=None,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app):
            streaming_client = app.state.passthrough_streaming_client
            buffered_client = app.state.passthrough_buffered_client
            assert not streaming_client.is_closed
            assert not buffered_client.is_closed

        assert streaming_client.is_closed
        assert buffered_client.is_closed


class TestAnthropicBaseUrl:
    def test_default_anthropic_base_has_no_v1_suffix(self) -> None:
        from luthien_proxy.passthrough_routes import UPSTREAM_BASES

        base = UPSTREAM_BASES["anthropic"]
        assert not base.endswith("/v1"), (
            f"UPSTREAM_BASES['anthropic'] must not include /v1 (got {base!r}); "
            "a request to /anthropic/v1/messages would become /v1/v1/messages upstream"
        )
        assert base == "https://api.anthropic.com"

    def test_upstream_url_construction_no_double_v1(self) -> None:
        from luthien_proxy.passthrough_routes import UPSTREAM_BASES

        base = UPSTREAM_BASES["anthropic"]
        path = "v1/messages"
        constructed = f"{base}/{path}"
        assert constructed == "https://api.anthropic.com/v1/messages"
        assert "/v1/v1/" not in constructed


class TestStreamingUpstreamError:
    def _make_streaming_app(self, streaming_client: MagicMock, deps=None) -> FastAPI:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[verify_passthrough_token] = lambda: "tok"
        app.dependency_overrides[verify_strict_client_key] = lambda: "tok"
        if deps is not None:
            app.state.dependencies = deps
        app.state.passthrough_buffered_client = _make_buffered_client()
        app.state.passthrough_streaming_client = streaming_client
        return app

    def _make_streaming_client(
        self, status_code: int, content: bytes = b"", content_type: str = "text/event-stream"
    ) -> MagicMock:
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.headers = {"content-type": content_type}

        async def aread():
            return content

        async def aiter_bytes():
            yield content

        mock_response.aread = aread
        mock_response.aiter_bytes = aiter_bytes

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_response)
        cm.__aexit__ = AsyncMock(return_value=None)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=cm)
        return mock_client

    @pytest.mark.parametrize("upstream_status", [401, 429, 500, 503])
    def test_streaming_upstream_error_returns_real_status(self, upstream_status: int) -> None:
        error_body = b'{"error": "upstream error"}'
        streaming_client = self._make_streaming_client(
            status_code=upstream_status,
            content=error_body,
            content_type="application/json",
        )
        app = self._make_streaming_app(streaming_client)

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_create.return_value = MagicMock(spec=NoOpRequestLogRecorder)
            with patch.dict("os.environ", {"ANTHROPIC_BASE_URL": "http://mock-upstream"}):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/anthropic/v1/messages",
                    json={"model": "claude-haiku-4-5", "max_tokens": 10, "messages": [], "stream": True},
                    headers={"anthropic-version": "2023-06-01"},
                )

        assert response.status_code == upstream_status, (
            f"Expected {upstream_status} from upstream to be forwarded to client, got {response.status_code}"
        )

    def test_streaming_2xx_returns_streaming_response(self) -> None:
        sse_chunk = b"data: {}\n\ndata: [DONE]\n\n"
        streaming_client = self._make_streaming_client(
            status_code=200,
            content=sse_chunk,
            content_type="text/event-stream",
        )
        app = self._make_streaming_app(streaming_client)

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_create.return_value = MagicMock(spec=NoOpRequestLogRecorder)
            with patch.dict("os.environ", {"ANTHROPIC_BASE_URL": "http://mock-upstream"}):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/anthropic/v1/messages",
                    json={"model": "claude-haiku-4-5", "max_tokens": 10, "messages": [], "stream": True},
                    headers={"anthropic-version": "2023-06-01"},
                )

        assert response.status_code == 200
        assert b"DONE" in response.content

    def test_streaming_forwards_upstream_content_type(self) -> None:
        streaming_client = self._make_streaming_client(
            status_code=200,
            content=b'[{"candidates": []}]',
            content_type="application/json",
        )
        app = self._make_streaming_app(streaming_client)

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_create.return_value = MagicMock(spec=NoOpRequestLogRecorder)
            with patch.dict("os.environ", {"GEMINI_BASE_URL": "http://mock-upstream", "GOOGLE_API_KEY": "test-key"}):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/gemini/v1beta/models/gemini-1.5-flash:streamGenerateContent",
                    json={"contents": [{"parts": [{"text": "hi"}]}]},
                )

        assert "application/json" in response.headers.get("content-type", "")


class TestMissingServerKey:
    def test_openai_missing_key_returns_503(self) -> None:
        app = _make_app()

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_create.return_value = MagicMock(spec=NoOpRequestLogRecorder)
            with patch.dict("os.environ", {}, clear=True):
                env = {k: v for k, v in __import__("os").environ.items() if k != "OPENAI_API_KEY"}
                with patch.dict("os.environ", env, clear=True):
                    client = TestClient(app, raise_server_exceptions=False)
                    response = client.post(
                        "/openai/v1/chat/completions",
                        json={"model": "gpt-4o", "messages": []},
                    )

        assert response.status_code == 503

    def test_gemini_missing_key_returns_503(self) -> None:
        app = _make_app()

        with patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create:
            mock_create.return_value = MagicMock(spec=NoOpRequestLogRecorder)
            env = {k: v for k, v in __import__("os").environ.items() if k != "GOOGLE_API_KEY"}
            with patch.dict("os.environ", env, clear=True):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/gemini/v1beta/models/gemini-1.5-flash:generateContent",
                    json={"contents": [{"parts": [{"text": "hi"}]}]},
                )

        assert response.status_code == 503
