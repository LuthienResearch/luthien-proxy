from __future__ import annotations

import tempfile
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from luthien_proxy.passthrough_auth import verify_passthrough_token, verify_strict_client_key
from luthien_proxy.passthrough_routes import router
from luthien_proxy.request_log.recorder import NoOpRequestLogRecorder, RequestLogRecorder
from luthien_proxy.utils.db import DatabasePool


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
            response = client.post("/openai/v1/chat/completions", json={"model": "gpt-4o", "messages": []})

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


def test_body_size_limit_413() -> None:
    app = _make_app()
    with patch("luthien_proxy.passthrough_routes.MAX_REQUEST_PAYLOAD_BYTES", 5):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/openai/v1/chat/completions", content=b"x" * 10)
    assert response.status_code == 413


def test_body_size_limit_normal_passes() -> None:
    app = _make_app()
    with patch("luthien_proxy.passthrough_routes.MAX_REQUEST_PAYLOAD_BYTES", 100):
        client = TestClient(app)
        response = client.post("/openai/v1/chat/completions", content=b"x" * 10)
    assert response.status_code != 413


def test_hop_by_hop_stripped() -> None:
    upstream_headers = {
        "content-type": "application/json",
        "transfer-encoding": "chunked",
        "set-cookie": "session=abc",
        "server": "nginx/1.0",
    }
    mock_buffered = _make_buffered_client(status_code=200, content=b"{}", headers=upstream_headers)
    app = _make_app(buffered_client=mock_buffered)
    client = TestClient(app)
    response = client.post("/openai/v1/chat/completions", json={"model": "gpt-4o"})
    assert "transfer-encoding" not in response.headers
    assert "set-cookie" not in response.headers
    assert "server" not in response.headers


def test_essential_headers_preserved() -> None:
    upstream_headers = {"content-type": "application/json"}
    mock_buffered = _make_buffered_client(status_code=200, content=b"{}", headers=upstream_headers)
    app = _make_app(buffered_client=mock_buffered)
    client = TestClient(app)
    response = client.post("/openai/v1/chat/completions", json={"model": "gpt-4o"})
    assert response.headers.get("content-type", "").startswith("application/json")


@pytest.fixture
def _policy_config_file():
    config_content = 'policy:\n  class: "luthien_proxy.policies.noop_policy:NoOpPolicy"\n  config: {}\n'
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        config_path = f.name
    yield config_path
    Path(config_path).unlink(missing_ok=True)


@pytest.fixture
def _mock_db_pool():
    mock = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock.get_pool = AsyncMock(return_value=mock_pool)
    mock.close = AsyncMock()
    mock.is_sqlite = False
    return mock


@pytest.fixture
def _mock_redis_client():
    mock = AsyncMock()
    mock.ping = AsyncMock()
    mock.close = AsyncMock()
    return mock


def test_lifespan_closes_httpx_clients_no_resource_warning(
    _policy_config_file, _mock_db_pool, _mock_redis_client
) -> None:
    from luthien_proxy.main import create_app

    app = create_app(
        api_key="test",
        admin_key=None,
        db_pool=_mock_db_pool,
        redis_client=_mock_redis_client,
        startup_policy_path=_policy_config_file,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        with TestClient(app):
            pass


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
