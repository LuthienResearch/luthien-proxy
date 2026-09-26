from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.requests import Request

from luthien_proxy.passthrough_capture import JsonObject
from luthien_proxy.passthrough_routes import (
    _passthrough,
    _RequestPayload,
    _require_passthrough_enabled,
    _response_body,
    _UpstreamTarget,
)


class _FakeDatabasePool:
    pass


class _PassthroughDependencies:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.db_pool = _FakeDatabasePool()
        self.enable_request_logging = True
        self.passthrough_buffered_client = client
        self.passthrough_streaming_client = client


class _CapturedRecorder:
    def __init__(self) -> None:
        self.on_commit: Callable[[str], Awaitable[None]] | None = None
        self.user_id: str | None = None

    def record_inbound_request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: JsonObject,
        session_id: str | None = None,
        user_id: str | None = None,
        model: str | None = None,
        is_streaming: bool = False,
        endpoint: str | None = None,
    ) -> None:
        self._inbound_request = (method, url, headers, body, session_id, model, is_streaming, endpoint)
        self.user_id = user_id

    def record_outbound_request(
        self,
        *,
        body: JsonObject,
        method: str = "POST",
        url: str | None = None,
        model: str | None = None,
        is_streaming: bool = False,
        endpoint: str | None = None,
    ) -> None:
        self._outbound_request = (body, method, url, model, is_streaming, endpoint)

    def record_inbound_response(
        self,
        *,
        status: int,
        body: JsonObject | None = None,
        headers: dict[str, str] | None = None,
        error: str | None = None,
    ) -> None:
        self._inbound_response = (status, body, headers, error)

    def record_outbound_response(
        self,
        *,
        body: JsonObject | None = None,
        status: int = 200,
        error: str | None = None,
    ) -> None:
        self._outbound_response = (body, status, error)

    def flush(self) -> None:
        pass


class _PassthroughSettings:
    def __init__(self, *, materialize_enabled: bool, trust_user_id_header: bool) -> None:
        self.passthrough_materialize_enabled = materialize_enabled
        self.trust_user_id_header = trust_user_id_header


def _make_passthrough_request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/openai/v1/chat/completions",
            "raw_path": b"/openai/v1/chat/completions",
            "query_string": b"",
            "headers": headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )


async def _invoke_passthrough(
    *,
    headers: list[tuple[bytes, bytes]],
    materialize_enabled: bool,
    trust_user_id_header: bool,
    is_streaming: bool,
) -> _CapturedRecorder:
    recorder = _CapturedRecorder()

    def capture_recorder(
        db_pool: _FakeDatabasePool,
        transaction_id: str,
        enabled: bool,
        *,
        on_commit: Callable[[str], Awaitable[None]] | None = None,
    ) -> _CapturedRecorder:
        assert isinstance(db_pool, _FakeDatabasePool)
        assert enabled
        assert transaction_id
        recorder.on_commit = on_commit
        return recorder

    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "response"}))
    async with httpx.AsyncClient(transport=transport) as client:
        dependencies = _PassthroughDependencies(client)
        with (
            patch(
                "luthien_proxy.passthrough_recording.get_settings",
                return_value=_PassthroughSettings(
                    materialize_enabled=materialize_enabled,
                    trust_user_id_header=trust_user_id_header,
                ),
            ),
            patch(
                "luthien_proxy.passthrough_recording.create_recorder",
                side_effect=capture_recorder,
            ),
            patch("luthien_proxy.passthrough_routes.get_dependencies", return_value=dependencies),
        ):
            await _passthrough(
                _make_passthrough_request(headers),
                _UpstreamTarget(
                    provider="openai",
                    path="v1/chat/completions",
                    base_url="https://upstream.test",
                    is_streaming=is_streaming,
                ),
                _RequestPayload(body_bytes=b'{"model":"gpt-4.1"}', body={"model": "gpt-4.1"}),
            )
    return recorder


@pytest.mark.parametrize("is_streaming", [False, True])
async def test_passthrough_wires_materialization_callback_when_enabled(is_streaming: bool) -> None:
    # Given
    materialized_transaction_ids: list[str] = []

    async def capture_materialization(_db_pool: _FakeDatabasePool, transaction_id: str) -> None:
        materialized_transaction_ids.append(transaction_id)

    with patch(
        "luthien_proxy.passthrough_recording.materialize_transaction",
        new=capture_materialization,
    ):
        # When
        recorder = await _invoke_passthrough(
            headers=[],
            materialize_enabled=True,
            trust_user_id_header=False,
            is_streaming=is_streaming,
        )

        # Then
        assert recorder.on_commit is not None
        await recorder.on_commit("transaction-123")
    assert materialized_transaction_ids == ["transaction-123"]


async def test_passthrough_omits_materialization_callback_when_disabled() -> None:
    # Given / When
    recorder = await _invoke_passthrough(
        headers=[],
        materialize_enabled=False,
        trust_user_id_header=False,
        is_streaming=False,
    )

    # Then
    assert recorder.on_commit is None


@pytest.mark.parametrize(
    ("headers", "trust_user_id_header", "expected_user_id"),
    [
        ([(b"x-luthien-user-id", b"trusted-user")], True, "trusted-user"),
        (
            [(b"authorization", b"Bearer x.eyJzdWIiOiJqd3QtdXNlciJ9.y")],
            False,
            "jwt-user",
        ),
        ([(b"x-luthien-user-id", b"untrusted-user")], False, None),
        ([], True, None),
    ],
)
async def test_passthrough_records_user_id_from_trusted_identity(
    headers: list[tuple[bytes, bytes]],
    trust_user_id_header: bool,
    expected_user_id: str | None,
) -> None:
    # Given / When
    recorder = await _invoke_passthrough(
        headers=headers,
        materialize_enabled=False,
        trust_user_id_header=trust_user_id_header,
        is_streaming=False,
    )

    # Then
    assert recorder.user_id == expected_user_id


def test_response_body_falls_back_to_replacement_text_for_invalid_utf8() -> None:
    # Given
    invalid_utf8 = b"\xff\xfe"

    # When
    body = _response_body(invalid_utf8)

    # Then
    assert body == {"body_text": "��"}


async def test_require_passthrough_enabled_rejects_when_disabled() -> None:
    # Given the feature flag is off (the default)
    with patch("luthien_proxy.passthrough_routes.get_settings") as mock_settings:
        mock_settings.return_value.passthrough_routes_enabled = False

        # When / Then the gate 404s so the routes look unmounted
        with pytest.raises(HTTPException) as exc_info:
            await _require_passthrough_enabled()
    assert exc_info.value.status_code == 404


async def test_require_passthrough_enabled_allows_when_enabled() -> None:
    # Given the feature flag is explicitly on
    with patch("luthien_proxy.passthrough_routes.get_settings") as mock_settings:
        mock_settings.return_value.passthrough_routes_enabled = True

        # When / Then the gate permits the request (no exception)
        assert await _require_passthrough_enabled() is None


async def test_streaming_passthrough_caps_captured_bytes_when_chunk_exceeds_remaining_budget() -> None:
    """A chunk larger than the remaining capture budget must not push captured
    bytes past PASSTHROUGH_STREAM_CAPTURE_MAX_BYTES.

    Regression: the loop used to check `captured_bytes < max_capture` and then
    unconditionally append the whole chunk, so a chunk that started under the
    cap but was itself larger than the remaining budget got fully retained and
    persisted -- defeating the bound the setting exists to enforce.
    """
    # Given: a tiny capture cap, and an upstream response streamed as two
    # chunks -- the first fits under the cap (5 of 10 bytes used), the second
    # is far larger than the 5 bytes of budget remaining.
    max_capture = 10
    captured_chunks: list[bytes] = []

    def spy_stream_body(provider: str, request: Request, chunks: list[bytes]) -> JsonObject:
        captured_chunks.extend(chunks)
        return {"stream_format": "openai-sse", "events": [], "final": None}

    async def body_gen() -> AsyncIterator[bytes]:
        yield b"12345"
        yield b"x" * 100

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body_gen())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dependencies = _PassthroughDependencies(client)
        recorder = _CapturedRecorder()
        with (
            patch(
                "luthien_proxy.passthrough_recording.get_settings",
                return_value=_PassthroughSettings(materialize_enabled=False, trust_user_id_header=False),
            ),
            patch("luthien_proxy.passthrough_recording.create_recorder", return_value=recorder),
            patch("luthien_proxy.passthrough_routes.get_dependencies", return_value=dependencies),
            patch("luthien_proxy.passthrough_routes.get_settings") as mock_settings,
            patch("luthien_proxy.passthrough_routes._stream_body", side_effect=spy_stream_body),
        ):
            mock_settings.return_value.passthrough_stream_capture_max_bytes = max_capture

            # When
            response = await _passthrough(
                _make_passthrough_request([]),
                _UpstreamTarget(
                    provider="openai",
                    path="v1/chat/completions",
                    base_url="https://upstream.test",
                    is_streaming=True,
                ),
                _RequestPayload(
                    body_bytes=b'{"model":"gpt-4.1","stream":true}', body={"model": "gpt-4.1", "stream": True}
                ),
            )
            assert isinstance(response, StreamingResponse)
            async for _ in response.body_iterator:
                pass

    # Then: total captured (and thus persisted) bytes never exceed the cap.
    assert sum(len(chunk) for chunk in captured_chunks) <= max_capture
