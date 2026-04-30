from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from luthien_proxy.passthrough_auth import verify_passthrough_token
from luthien_proxy.passthrough_routes import router
from luthien_proxy.request_log.recorder import NoOpRequestLogRecorder, RequestLogRecorder
from luthien_proxy.utils.db import DatabasePool


def _make_app(deps=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_passthrough_token] = lambda: "tok"
    if deps is not None:
        app.state.dependencies = deps
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

        with (
            patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create,
            patch("luthien_proxy.passthrough_routes._buffered_client") as mock_client,
        ):
            mock_recorder = MagicMock(spec=NoOpRequestLogRecorder)
            mock_create.return_value = mock_recorder

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {}
            mock_client.request = AsyncMock(return_value=mock_response)

            client = TestClient(app, raise_server_exceptions=True)
            client.post("/openai/v1/chat/completions", json={"model": "gpt-4o", "messages": []})

            mock_create.assert_called_once()
            assert mock_create.call_args.kwargs["enabled"] is False

    def test_creates_noop_recorder_when_no_deps(self) -> None:
        app = _make_app(deps=None)

        with (
            patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create,
            patch("luthien_proxy.passthrough_routes._buffered_client") as mock_client,
        ):
            mock_recorder = MagicMock(spec=NoOpRequestLogRecorder)
            mock_create.return_value = mock_recorder

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {}
            mock_client.request = AsyncMock(return_value=mock_response)

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

        with (
            patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create,
            patch("luthien_proxy.passthrough_routes._buffered_client") as mock_client,
        ):
            mock_recorder = MagicMock(spec=RequestLogRecorder)
            mock_create.return_value = mock_recorder

            def capture_inbound(**kwargs):
                captured.update(kwargs)

            mock_recorder.record_inbound_request.side_effect = capture_inbound

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {}
            mock_client.request = AsyncMock(return_value=mock_response)

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
        app = _make_app(deps)

        with (
            patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create,
            patch("luthien_proxy.passthrough_routes._buffered_client") as mock_client,
        ):
            mock_recorder = MagicMock(spec=RequestLogRecorder)
            mock_create.return_value = mock_recorder

            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.content = b'{"id": "chatcmpl-123"}'
            mock_response.headers = {}
            mock_client.request = AsyncMock(return_value=mock_response)

            client = TestClient(app, raise_server_exceptions=True)
            client.post("/openai/v1/chat/completions", json={"model": "gpt-4o", "messages": []})

        mock_recorder.record_inbound_response.assert_called_once_with(status=201)
        mock_recorder.flush.assert_called_once()

    def test_flush_called_on_upstream_error(self) -> None:
        import httpx

        deps = _make_deps(enable_request_logging=True)
        app = _make_app(deps)

        with (
            patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create,
            patch("luthien_proxy.passthrough_routes._buffered_client") as mock_client,
        ):
            mock_recorder = MagicMock(spec=RequestLogRecorder)
            mock_create.return_value = mock_recorder
            mock_client.request = AsyncMock(side_effect=httpx.ConnectError("refused"))

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

    with (
        patch("luthien_proxy.passthrough_routes.create_recorder") as mock_create,
        patch("luthien_proxy.passthrough_routes._buffered_client") as mock_client,
    ):
        mock_recorder = MagicMock(spec=RequestLogRecorder)
        mock_create.return_value = mock_recorder

        def capture(**kwargs):
            captured_headers.update(kwargs.get("headers", {}))

        mock_recorder.record_inbound_request.side_effect = capture

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"{}"
        mock_response.headers = {}
        mock_client.request = AsyncMock(return_value=mock_response)

        client = TestClient(app)
        client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers={sensitive_header: "secret-value"},
        )

    assert sensitive_header in captured_headers
