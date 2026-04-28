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

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from luthien_proxy.passthrough_auth import verify_passthrough_token
from luthien_proxy.request_log.recorder import create_recorder

router = APIRouter()
logger = logging.getLogger(__name__)

UPSTREAM_BASES = {
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "anthropic": "https://api.anthropic.com/v1",
}


def _upstream_base(provider: str) -> str:
    """Return the upstream base URL for a provider.

    Checks OPENAI_BASE_URL and GEMINI_BASE_URL env vars at call time so tests
    can redirect traffic to mock servers without restarting the gateway.
    """
    env_overrides: dict[str, str | None] = {
        "openai": os.environ.get("OPENAI_BASE_URL"),
        "gemini": os.environ.get("GEMINI_BASE_URL"),
    }
    return env_overrides.get(provider) or UPSTREAM_BASES[provider]


# Streaming client: generous read timeout for long-running/thinking responses
_streaming_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=30.0))

# Buffered client: standard short timeout
_buffered_client = httpx.AsyncClient(timeout=30.0)

# Headers stripped from inbound before forwarding (httpx sets these itself)
_STRIP_INBOUND = frozenset({"host", "content-length"})

# Auth headers stripped from all outbound — re-injected per provider below
_STRIP_AUTH = frozenset({"authorization", "x-api-key", "x-anthropic-api-key", "x-goog-api-key"})


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
        if key:
            headers["authorization"] = f"Bearer {key}"
    elif provider == "gemini":
        key = os.environ.get("GOOGLE_API_KEY", "")
        if key:
            headers["x-goog-api-key"] = key
    elif provider == "anthropic":
        # Forward the client's own auth — this is an alias to the existing /v1/
        for auth_header in ("authorization", "x-api-key", "x-anthropic-api-key"):
            if (val := request.headers.get(auth_header)) is not None:
                headers[auth_header] = val

    if "user-agent" not in headers:
        headers["user-agent"] = "luthien-passthrough/0.1"

    return headers


async def _handle_passthrough(request: Request, provider: str, path: str) -> Response:
    body = await request.body()
    upstream_url = f"{_upstream_base(provider)}/{path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    headers = _build_outbound_headers(request, provider)

    request.state.luthien_session_id = request.headers.get("x-luthien-session-id")
    request.state.luthien_agent = request.headers.get("x-luthien-agent")
    request.state.luthien_model = request.headers.get("x-luthien-model")

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
        body={},
        session_id=request.state.luthien_session_id,
        agent=request.state.luthien_agent,
        model=request.state.luthien_model,
        endpoint=f"/{provider}/{path}",
    )

    streaming = _is_streaming(path, body)

    if streaming:

        async def stream_chunks():
            status = 200
            error: str | None = None
            try:
                async with _streaming_client.stream(
                    request.method,
                    upstream_url,
                    headers=headers,
                    content=body or None,
                ) as response:
                    status = response.status_code
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except httpx.RequestError as exc:
                logger.warning("Streaming passthrough error for %s/%s: %s", provider, path, repr(exc))
                status = 502
                error = repr(exc)
            finally:
                recorder.record_inbound_response(status=status, error=error)
                recorder.flush()

        return StreamingResponse(stream_chunks(), media_type="text/event-stream")

    try:
        response = await _buffered_client.request(
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

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=dict(response.headers),
    )


@router.api_route("/openai/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def openai_passthrough(
    request: Request,
    path: str,
    _token: str = Depends(verify_passthrough_token),
) -> Response:
    # Track A bridge passthrough — replaced by native pipeline in Track B (#563-569)
    return await _handle_passthrough(request, "openai", path)


@router.api_route("/gemini/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def gemini_passthrough(
    request: Request,
    path: str,
    _token: str = Depends(verify_passthrough_token),
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
