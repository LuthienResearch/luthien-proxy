"""Passthrough routes for /openai/*, /gemini/*, /anthropic/* prefixes.

Bridges OpenAI, Gemini, and Anthropic traffic through the Luthien gateway.
Injects server-side API keys, strips internal x-luthien-* headers from outbound,
and streams responses for streaming endpoints.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from urllib.parse import parse_qsl, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from luthien_proxy.passthrough_auth import verify_passthrough_token, verify_strict_client_key
from luthien_proxy.request_log.recorder import create_recorder
from luthien_proxy.utils.constants import MAX_REQUEST_PAYLOAD_BYTES

router = APIRouter()
logger = logging.getLogger(__name__)

UPSTREAM_BASES = {
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "anthropic": "https://api.anthropic.com",
}


def _upstream_base(provider: str) -> str:
    env_overrides: dict[str, str | None] = {
        "openai": os.environ.get("OPENAI_BASE_URL"),
        "gemini": os.environ.get("GEMINI_BASE_URL"),
        "anthropic": os.environ.get("ANTHROPIC_BASE_URL"),
    }
    return env_overrides.get(provider) or UPSTREAM_BASES[provider]


# Headers stripped from inbound before forwarding (httpx sets these itself)
_STRIP_INBOUND = frozenset({"host", "content-length"})

# Auth headers stripped from all outbound — re-injected per provider below
_STRIP_AUTH = frozenset({"authorization", "x-api-key", "x-anthropic-api-key", "x-goog-api-key"})

# Headers stripped from upstream responses that httpx has already handled
# (httpx auto-decompresses, so forwarding content-encoding would mismatch the body)
_STRIP_RESPONSE = frozenset({"content-encoding", "content-length"})

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
DANGEROUS_RESPONSE_HEADERS = frozenset({"set-cookie", "server", "x-powered-by"})


def get_streaming_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.passthrough_streaming_client


def get_buffered_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.passthrough_buffered_client


def _is_streaming(path: str, body: bytes) -> bool:
    if ":streamGenerateContent" in path:
        return True
    try:
        data = json.loads(body)
        return bool(data.get("stream", False))
    except (json.JSONDecodeError, AttributeError, ValueError):
        return False


def _build_outbound_headers(request: Request, provider: str) -> dict[str, str]:
    """Build headers for the upstream request.

    Strips internal x-luthien-* headers and replaces auth headers with
    server-side credentials for OpenAI/Gemini. Anthropic alias forwards
    the client's auth as-is.
    """
    headers: dict[str, str] = {}
    for k, v in request.headers.items():
        k_lower = k.lower()
        if k_lower in _STRIP_INBOUND:
            continue
        if k_lower in _STRIP_AUTH:
            continue
        if k_lower.startswith("x-luthien-"):
            continue
        headers[k_lower] = v

    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise HTTPException(status_code=503, detail="OpenAI API key not configured on server")
        headers["authorization"] = f"Bearer {key}"
    elif provider == "gemini":
        key = os.environ.get("GOOGLE_API_KEY", "")
        if not key:
            raise HTTPException(status_code=503, detail="Google API key not configured on server")
        headers["x-goog-api-key"] = key
    elif provider == "anthropic":
        # Forward the client's own auth — this is an alias to the existing /v1/
        for auth_header in ("authorization", "x-api-key", "x-anthropic-api-key"):
            if (val := request.headers.get(auth_header)) is not None:
                headers[auth_header] = val

    if "user-agent" not in headers:
        headers["user-agent"] = "luthien-passthrough/0.1"

    return headers


def _safe_response_headers(response_headers: httpx.Headers) -> dict[str, str]:
    return {
        k: v
        for k, v in response_headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
        and k.lower() not in DANGEROUS_RESPONSE_HEADERS
        and k.lower() not in _STRIP_RESPONSE
    }


async def _handle_passthrough(request: Request, provider: str, path: str) -> Response:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_PAYLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Request payload too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid content-length header")

    body = await request.body()

    if len(body) > MAX_REQUEST_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Request payload too large")
    upstream_url = f"{_upstream_base(provider)}/{path}"
    if request.url.query:
        query = request.url.query
        if provider == "gemini":
            # Strip ?key= to prevent clients from bypassing server-injected auth;
            # the server key is injected via x-goog-api-key header instead.
            params = [(k, v) for k, v in parse_qsl(query) if k.lower() != "key"]
            query = urlencode(params)
        if query:
            upstream_url = f"{upstream_url}?{query}"

    headers = _build_outbound_headers(request, provider)

    deps = getattr(request.app.state, "dependencies", None)
    recorder = create_recorder(
        db_pool=deps.db_pool if deps is not None else None,
        transaction_id=str(uuid.uuid4()),
        enabled=deps.enable_request_logging if deps is not None else False,
    )
    recorder.record_inbound_request(
        method=request.method,
        url=str(request.url),
        headers=dict(request.headers),
        body={},  # passthrough bodies may be non-JSON or very large; not logged
        session_id=request.headers.get("x-luthien-session-id"),
        agent=request.headers.get("x-luthien-agent"),
        model=request.headers.get("x-luthien-model"),
        endpoint=f"/{provider}/{path}",
    )

    streaming = _is_streaming(path, body)
    streaming_client = get_streaming_client(request)
    buffered_client = get_buffered_client(request)

    if streaming:
        # We must peek at the upstream response status before committing an HTTP
        # status to the client.  StreamingResponse locks in 200 the moment it is
        # returned, so a 4xx/5xx from upstream would be silently misreported.
        # Strategy: open the upstream connection, read the status + headers, then
        # either return a plain Response for non-2xx or hand off to a generator
        # for 2xx streaming.
        try:
            upstream_cm = streaming_client.stream(
                request.method,
                upstream_url,
                headers=headers,
                content=body or None,
            )
            response = await upstream_cm.__aenter__()
        except httpx.RequestError as exc:
            logger.warning("Streaming passthrough error for %s/%s: %s", provider, path, repr(exc))
            recorder.record_inbound_response(status=502, error=repr(exc))
            recorder.flush()
            raise HTTPException(status_code=502, detail="Failed to connect to upstream API")

        if response.status_code >= 300:
            # Non-2xx: buffer the error body and return a plain Response so the
            # client sees the real status code.
            try:
                error_body = await response.aread()
            finally:
                await upstream_cm.__aexit__(None, None, None)
            recorder.record_inbound_response(status=response.status_code)
            recorder.flush()
            safe_headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in HOP_BY_HOP_HEADERS
                and k.lower() not in DANGEROUS_RESPONSE_HEADERS
                and k.lower() not in _STRIP_RESPONSE
            }
            return Response(
                content=error_body,
                status_code=response.status_code,
                headers=safe_headers,
            )

        # 2xx: stream the body.  Forward the upstream Content-Type so clients
        # that branch on it (e.g. Gemini JSON vs SSE) get the right value.
        # Also forward other safe upstream headers (rate-limit, request-id, etc.)
        # to match the behaviour of the buffered path.
        upstream_content_type = response.headers.get("content-type", "text/event-stream")
        safe_headers = _safe_response_headers(response.headers)

        async def stream_chunks():
            status = response.status_code
            error: str | None = None
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            except httpx.RequestError as exc:
                logger.warning("Streaming passthrough error for %s/%s: %s", provider, path, repr(exc))
                status = 502
                error = repr(exc)
            finally:
                await upstream_cm.__aexit__(None, None, None)
                recorder.record_inbound_response(status=status, error=error)
                recorder.flush()

        return StreamingResponse(stream_chunks(), media_type=upstream_content_type, headers=safe_headers)

    try:
        response = await buffered_client.request(
            request.method,
            upstream_url,
            headers=headers,
            content=body or None,
        )
    except httpx.RequestError as exc:
        logger.warning("Buffered passthrough error for %s/%s: %s", provider, path, repr(exc))
        recorder.record_inbound_response(status=502, error=repr(exc))
        recorder.flush()
        raise HTTPException(status_code=502, detail="Failed to connect to upstream API")

    recorder.record_inbound_response(status=response.status_code)
    recorder.flush()

    safe_headers = _safe_response_headers(response.headers)
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=safe_headers,
    )


@router.api_route("/openai/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def openai_passthrough(
    request: Request,
    path: str,
    _token: str = Depends(verify_strict_client_key),
) -> Response:
    # Track A bridge passthrough — replaced by native pipeline in Track B (#563-569)
    return await _handle_passthrough(request, "openai", path)


@router.api_route("/gemini/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def gemini_passthrough(
    request: Request,
    path: str,
    _token: str = Depends(verify_strict_client_key),
) -> Response:
    # Track A bridge passthrough — replaced by native pipeline in Track B (#563-569)
    return await _handle_passthrough(request, "gemini", path)


@router.api_route("/anthropic/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def anthropic_alias_passthrough(
    request: Request,
    path: str,
    _token: str = Depends(verify_passthrough_token),
) -> Response:
    # Track A bridge passthrough — replaced by native pipeline in Track B (#563-569)
    return await _handle_passthrough(request, "anthropic", path)


__all__ = ["router"]
