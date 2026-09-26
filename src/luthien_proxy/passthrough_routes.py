"""OpenAI and Gemini passthrough routes with full request_log body capture."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, assert_never

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from luthien_proxy.dependencies import get_dependencies
from luthien_proxy.passthrough_capture import (
    JsonObject,
    build_passthrough_headers,
    build_upstream_url,
    json_loads,
    parse_gemini_model,
    parse_openai_model,
    reassemble_gemini_json_array_stream,
    reassemble_gemini_sse_stream,
    reassemble_openai_sse_stream,
)
from luthien_proxy.passthrough_capture import (
    client_response_headers as _client_response_headers,
)
from luthien_proxy.passthrough_recording import create_passthrough_recorder
from luthien_proxy.request_log.recorder import RequestLogRecorder
from luthien_proxy.request_log.sanitize import sanitize_url
from luthien_proxy.settings import get_settings

router = APIRouter(tags=["passthrough"])

_OPENAI_BASE_URL = "https://api.openai.com"
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"


async def _require_passthrough_enabled() -> None:
    """Gate the passthrough routes behind PASSTHROUGH_ROUTES_ENABLED (default off).

    These routes forward client-supplied upstream credentials, so an always-on
    deployment would act as an open relay and let anyone with network reach write
    request_logs. When disabled we 404 so the routes are indistinguishable from
    unmounted paths.
    """
    if not get_settings().passthrough_routes_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


@dataclass(frozen=True, slots=True)
class _UpstreamTarget:
    provider: Literal["openai", "gemini"]
    path: str
    base_url: str
    is_streaming: bool

    @property
    def endpoint(self) -> str:
        return f"/{self.provider}/{self.path}"


@dataclass(frozen=True, slots=True)
class _RequestPayload:
    body_bytes: bytes
    body: JsonObject


@dataclass(frozen=True, slots=True)
class _StreamContext:
    request: Request
    client: httpx.AsyncClient
    target: _UpstreamTarget
    upstream_url: str
    forwarded_headers: dict[str, str]
    payload: _RequestPayload
    recorder: RequestLogRecorder


async def _json_body(request: Request) -> _RequestPayload:
    body_bytes = await request.body()
    if not body_bytes:
        return _RequestPayload(body_bytes=body_bytes, body={})
    parsed = json_loads(body_bytes)
    if isinstance(parsed, dict):
        return _RequestPayload(body_bytes=body_bytes, body=parsed)
    return _RequestPayload(body_bytes=body_bytes, body={"body": parsed})


def _is_openai_stream(path: str, body: JsonObject) -> bool:
    stream = body.get("stream")
    return stream is True or path.endswith("/stream")


def _is_gemini_stream(path: str, request: Request) -> bool:
    return "streamGenerateContent" in path or request.query_params.get("alt") == "sse"


def _response_body(response_bytes: bytes) -> JsonObject:
    if not response_bytes:
        return {}
    try:
        parsed = json_loads(response_bytes)
    except ValueError:
        return {"body_text": response_bytes.decode(errors="replace")}
    if isinstance(parsed, dict):
        return parsed
    return {"body": parsed}


def _stream_body(provider: Literal["openai", "gemini"], request: Request, chunks: list[bytes]) -> JsonObject:
    match provider:
        case "openai":
            return reassemble_openai_sse_stream(chunks)
        case "gemini":
            if request.query_params.get("alt") == "sse":
                return reassemble_gemini_sse_stream(chunks)
            return reassemble_gemini_json_array_stream(chunks)
        case unreachable:
            assert_never(unreachable)


def _request_error_text(error: httpx.RequestError, upstream_url: str, forwarded_headers: dict[str, str]) -> str:
    text = f"{type(error).__name__}: {error!s}"
    text = text.replace(upstream_url, sanitize_url(upstream_url))
    for value in forwarded_headers.values():
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def _upstream_error_response(
    recorder: RequestLogRecorder,
    error: httpx.RequestError,
    upstream_url: str,
    forwarded_headers: dict[str, str],
) -> JSONResponse:
    error_text = _request_error_text(error, upstream_url, forwarded_headers)
    recorder.record_inbound_response(status=502, error=error_text)
    recorder.record_outbound_response(status=502, error=error_text)
    recorder.flush()
    return JSONResponse(status_code=502, content={"error": "upstream request failed"})


async def _passthrough(request: Request, target: _UpstreamTarget, payload: _RequestPayload) -> Response:
    deps = get_dependencies(request)
    recorder, session_id, user_id = create_passthrough_recorder(request.headers, str(uuid.uuid4()), deps)
    upstream_url = build_upstream_url(target.base_url, target.path, request.url.query)
    forwarded_headers = build_passthrough_headers(request.headers.items())
    model = (
        parse_openai_model(payload.body, request.headers.get("x-luthien-model"))
        if target.provider == "openai"
        else parse_gemini_model(target.path, payload.body, request.headers.get("x-luthien-model"))
    )
    recorder.record_inbound_request(
        method=request.method,
        url=sanitize_url(str(request.url)),
        headers=dict(request.headers),
        body=payload.body,
        session_id=session_id,
        user_id=user_id,
        model=model,
        endpoint=target.endpoint,
        is_streaming=target.is_streaming,
    )
    recorder.record_outbound_request(
        method=request.method,
        url=sanitize_url(upstream_url),
        body=payload.body,
        model=model,
        endpoint=target.endpoint,
        is_streaming=target.is_streaming,
    )
    client = deps.passthrough_streaming_client if target.is_streaming else deps.passthrough_buffered_client
    if client is None:
        return Response(status_code=503, content=b"passthrough client not initialized")
    if target.is_streaming:
        return await _streaming_passthrough(
            _StreamContext(
                request=request,
                client=client,
                target=target,
                upstream_url=upstream_url,
                forwarded_headers=forwarded_headers,
                payload=payload,
                recorder=recorder,
            )
        )
    try:
        upstream = await client.request(
            request.method, upstream_url, headers=forwarded_headers, content=payload.body_bytes
        )
    except httpx.RequestError as exc:
        return _upstream_error_response(recorder, exc, upstream_url, forwarded_headers)
    body = _response_body(upstream.content)
    recorder.record_inbound_response(status=upstream.status_code, body=body, headers=dict(upstream.headers))
    recorder.record_outbound_response(status=upstream.status_code, body=body)
    recorder.flush()
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_client_response_headers(upstream.headers),
    )


async def _streaming_passthrough(context: _StreamContext) -> Response:
    upstream_request = context.client.build_request(
        context.request.method,
        context.upstream_url,
        headers=context.forwarded_headers,
        content=context.payload.body_bytes,
    )
    try:
        upstream = await context.client.send(upstream_request, stream=True)
    except httpx.RequestError as exc:
        return _upstream_error_response(context.recorder, exc, context.upstream_url, context.forwarded_headers)
    if upstream.status_code >= 400:
        body_bytes = await upstream.aread()
        await upstream.aclose()
        body = _response_body(body_bytes)
        context.recorder.record_inbound_response(status=upstream.status_code, body=body, headers=dict(upstream.headers))
        context.recorder.record_outbound_response(status=upstream.status_code, body=body)
        context.recorder.flush()
        return Response(
            content=body_bytes,
            status_code=upstream.status_code,
            headers=_client_response_headers(upstream.headers),
        )

    async def stream() -> AsyncIterator[bytes]:
        chunks: list[bytes] = []
        captured_bytes = 0
        max_capture = get_settings().passthrough_stream_capture_max_bytes
        truncated = False
        try:
            async for chunk in upstream.aiter_bytes():
                if captured_bytes < max_capture:
                    chunks.append(chunk)
                    captured_bytes += len(chunk)
                    if captured_bytes >= max_capture:
                        truncated = True
                yield chunk
        finally:
            await upstream.aclose()
            body = _stream_body(context.target.provider, context.request, chunks)
            if truncated:
                body = {**body, "capture_truncated": True}
            context.recorder.record_inbound_response(
                status=upstream.status_code, body=body, headers=dict(upstream.headers)
            )
            context.recorder.record_outbound_response(status=upstream.status_code, body=body)
            context.recorder.flush()

    return StreamingResponse(
        stream(), status_code=upstream.status_code, headers=_client_response_headers(upstream.headers)
    )


@router.api_route("/openai/{path:path}", methods=["GET", "POST"], dependencies=[Depends(_require_passthrough_enabled)])
async def openai_passthrough(request: Request, path: str) -> Response:
    payload = await _json_body(request)
    return await _passthrough(
        request,
        _UpstreamTarget(
            provider="openai",
            path=path,
            base_url=os.getenv("OPENAI_BASE_URL", _OPENAI_BASE_URL),
            is_streaming=_is_openai_stream(path, payload.body),
        ),
        payload,
    )


@router.api_route("/gemini/{path:path}", methods=["GET", "POST"], dependencies=[Depends(_require_passthrough_enabled)])
async def gemini_passthrough(request: Request, path: str) -> Response:
    payload = await _json_body(request)
    return await _passthrough(
        request,
        _UpstreamTarget(
            provider="gemini",
            path=path,
            base_url=os.getenv("GEMINI_BASE_URL", _GEMINI_BASE_URL),
            is_streaming=_is_gemini_stream(path, request),
        ),
        payload,
    )


__all__ = ["router"]
