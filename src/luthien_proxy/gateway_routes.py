"""LLM gateway API routes with unified request processing."""

from __future__ import annotations

import logging
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.credential_manager import AuthMode, CredentialManager, is_anthropic_api_key
from luthien_proxy.dependencies import (
    get_anthropic_client,
    get_anthropic_policy,
    get_api_key,
    get_credential_manager,
    get_db_pool,
    get_dependencies,
    get_emitter,
    get_usage_collector,
)
from luthien_proxy.llm import anthropic_client_cache
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.pipeline import process_anthropic_request
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
)
from luthien_proxy.usage_telemetry.collector import UsageCollector
from luthien_proxy.utils import db

router = APIRouter(tags=["gateway"])
security = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)

ANTHROPIC_API_BASE = "https://api.anthropic.com"

# Shared httpx client for the passthrough proxy.  Reusing a single client
# avoids creating and tearing down a connection pool on every request.
_passthrough_client = httpx.AsyncClient(timeout=30.0)


# === AUTH ===


async def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    api_key: str | None = Depends(get_api_key),
    credential_manager: CredentialManager | None = Depends(get_credential_manager),
) -> str:
    """Verify API key, supporting proxy_key, passthrough, and both auth modes."""
    bearer_token = credentials.credentials if credentials else None
    api_key_header = request.headers.get("x-api-key")
    token = bearer_token or api_key_header
    if not token:
        raise HTTPException(status_code=401, detail="Missing API key")
    is_bearer = bearer_token is not None

    if credential_manager is None:
        if api_key is not None and secrets.compare_digest(token, api_key):
            return token
        raise HTTPException(status_code=401, detail="Invalid API key")

    auth_mode = credential_manager.config.auth_mode

    if auth_mode == AuthMode.PROXY_KEY:
        if api_key is not None and secrets.compare_digest(token, api_key):
            return token
        raise HTTPException(status_code=401, detail="Invalid API key")

    if auth_mode == AuthMode.PASSTHROUGH:
        if credential_manager.config.validate_credentials:
            if not await credential_manager.validate_credential(token, is_bearer=is_bearer):
                raise HTTPException(status_code=401, detail="Invalid credential")
        return token

    # BOTH mode: try proxy key first, fall through to passthrough
    if api_key is not None and secrets.compare_digest(token, api_key):
        return token
    if credential_manager.config.validate_credentials:
        if not await credential_manager.validate_credential(token, is_bearer=is_bearer):
            raise HTTPException(status_code=401, detail="Invalid API key or credential")
    return token


async def resolve_anthropic_client(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    api_key: str | None = Depends(get_api_key),
    credential_manager: CredentialManager | None = Depends(get_credential_manager),
    base_client: AnthropicClient | None = Depends(get_anthropic_client),
    _: str = Depends(verify_token),
) -> AnthropicClient:
    """Verify auth and resolve the Anthropic client for this request."""
    bearer_token = credentials.credentials if credentials else None
    api_key_header = request.headers.get("x-api-key")
    token = bearer_token or api_key_header
    if not token:
        raise HTTPException(status_code=401, detail="Missing API key")
    is_bearer = bearer_token is not None

    auth_mode = credential_manager.config.auth_mode if credential_manager else AuthMode.PROXY_KEY
    base_url = base_client._base_url if base_client else None

    async def _record_credential_type(cred_type: str) -> None:
        """Best-effort write of observed credential type for /health visibility."""
        if auth_mode == AuthMode.PROXY_KEY:
            return
        deps = getattr(request.app.state, "dependencies", None)
        if deps:
            deps.last_credential_info = {"type": cred_type, "timestamp": time.time()}

    # Explicit x-anthropic-api-key overrides upstream credential
    explicit_key = request.headers.get("x-anthropic-api-key")
    if explicit_key is not None:
        if not explicit_key.strip():
            raise HTTPException(status_code=401, detail="x-anthropic-api-key header is empty")
        await _record_credential_type("client_api_key")
        return await anthropic_client_cache.get_client(explicit_key, auth_type="api_key", base_url=base_url)

    # Passthrough: forward the request credential to Anthropic
    matches_proxy_key = api_key is not None and secrets.compare_digest(token, api_key)
    use_passthrough = not matches_proxy_key or auth_mode == AuthMode.PASSTHROUGH
    if use_passthrough:
        # OAuth tokens arrive via Bearer header and are not Anthropic API keys.
        # In passthrough mode, x-api-key tokens that don't look like Anthropic
        # API keys (sk-ant-*) are also treated as OAuth tokens — Claude Code
        # may send OAuth tokens via either header depending on configuration.
        if not is_anthropic_api_key(token):
            cred_type = "oauth" if is_bearer else "oauth_via_api_key"
            await _record_credential_type(cred_type)
            auth_type = "auth_token" if is_bearer else "api_key"
            return await anthropic_client_cache.get_client(token, auth_type=auth_type, base_url=base_url)
        await _record_credential_type("client_api_key")
        return await anthropic_client_cache.get_client(token, auth_type="api_key", base_url=base_url)

    # Proxy key fallback: use the server's configured client
    if base_client is None:
        raise HTTPException(
            status_code=500,
            detail="No Anthropic credentials available (set ANTHROPIC_API_KEY or use passthrough auth)",
        )
    await _record_credential_type("proxy_key_fallback")
    return base_client


# === ROUTES ===


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    anthropic_client: AnthropicClient = Depends(resolve_anthropic_client),
    anthropic_policy: AnthropicExecutionInterface = Depends(get_anthropic_policy),
    emitter: EventEmitterProtocol = Depends(get_emitter),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
    usage_collector: UsageCollector | None = Depends(get_usage_collector),
):
    """Anthropic Messages API endpoint (native Anthropic path)."""
    deps = get_dependencies(request)
    return await process_anthropic_request(
        request=request,
        policy=anthropic_policy,
        anthropic_client=anthropic_client,
        emitter=emitter,
        db_pool=db_pool,
        enable_request_logging=deps.enable_request_logging,
        usage_collector=usage_collector,
    )


# IMPORTANT: This catch-all MUST be registered after /v1/messages to avoid
# shadowing it.  FastAPI matches routes in registration order.
@router.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_passthrough(
    request: Request,
    path: str,
    _: str = Depends(verify_token),
):
    """Transparent proxy for /v1/* endpoints not explicitly handled.

    Forwards requests to the Anthropic API so Claude Code can use endpoints
    like /v1/messages/count_tokens, /v1/models, etc. without getting 404.
    """
    # Build upstream URL
    upstream_url = f"{ANTHROPIC_API_BASE}/v1/{path}"

    # Forward relevant headers (auth, content-type, anthropic-specific)
    forward_headers: dict[str, str] = {}
    for key in ("authorization", "x-api-key", "content-type", "anthropic-version", "anthropic-beta"):
        if value := request.headers.get(key):
            forward_headers[key] = value

    # Read request body (if any)
    body = await request.body()

    try:
        upstream_response = await _passthrough_client.request(
            method=request.method,
            url=upstream_url,
            headers=forward_headers,
            content=body if body else None,
            params=dict(request.query_params),
        )
    except httpx.RequestError as e:
        logger.warning("Proxy passthrough error for /v1/%s: %s", path, repr(e))
        raise HTTPException(status_code=502, detail="Failed to connect to upstream API")

    # Forward the response back to the client
    response_headers = {}
    for key in ("content-type", "x-request-id", "request-id"):
        if value := upstream_response.headers.get(key):
            response_headers[key] = value

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
    )


__all__ = ["router"]
