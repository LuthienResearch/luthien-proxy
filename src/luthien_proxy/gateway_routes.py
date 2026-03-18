"""LLM gateway API routes with unified request processing."""

from __future__ import annotations

import json
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.credential_manager import AuthMode, CredentialManager
from luthien_proxy.dependencies import (
    get_anthropic_client,
    get_anthropic_policy,
    get_api_key,
    get_credential_manager,
    get_db_pool,
    get_dependencies,
    get_emitter,
    get_llm_client,
    get_policy,
    get_usage_collector,
)
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.client import LLMClient
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.pipeline import process_anthropic_request, process_llm_request
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
)
from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface
from luthien_proxy.usage_telemetry.collector import UsageCollector
from luthien_proxy.utils import db

router = APIRouter(tags=["gateway"])
security = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)

# Redis key for tracking the most recently observed upstream credential type.
# Read by /health to power the billing-mode badge in the UI.
LAST_CRED_TYPE_KEY = "luthien:auth:last_credential_type"
LAST_CRED_TYPE_TTL = 86400  # 24 hours


# === AUTH ===


async def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    api_key: str = Depends(get_api_key),
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
        if secrets.compare_digest(token, api_key):
            return token
        raise HTTPException(status_code=401, detail="Invalid API key")

    auth_mode = credential_manager.config.auth_mode

    if auth_mode == AuthMode.PROXY_KEY:
        if secrets.compare_digest(token, api_key):
            return token
        raise HTTPException(status_code=401, detail="Invalid API key")

    if auth_mode == AuthMode.PASSTHROUGH:
        if credential_manager.config.validate_credentials:
            if not await credential_manager.validate_credential(token, is_bearer=is_bearer):
                raise HTTPException(status_code=401, detail="Invalid credential")
        return token

    # BOTH mode: try proxy key first, fall through to passthrough
    if secrets.compare_digest(token, api_key):
        return token
    if credential_manager.config.validate_credentials:
        if not await credential_manager.validate_credential(token, is_bearer=is_bearer):
            raise HTTPException(status_code=401, detail="Invalid API key or credential")
    return token


async def resolve_anthropic_client(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    api_key: str = Depends(get_api_key),
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
        """Best-effort write of observed credential type to Redis for /health visibility."""
        if auth_mode == AuthMode.PROXY_KEY:
            return  # proxy_key mode is surfaced statically via auth_mode; no need to record
        redis = credential_manager._redis if credential_manager else None
        if redis is None:
            return
        try:
            payload = json.dumps({"type": cred_type, "timestamp": time.time()})
            await redis.setex(LAST_CRED_TYPE_KEY, LAST_CRED_TYPE_TTL, payload)
        except Exception:
            logger.warning("Failed to record credential type to Redis", exc_info=True)

    # Explicit x-anthropic-api-key overrides upstream credential
    explicit_key = request.headers.get("x-anthropic-api-key")
    if explicit_key is not None:
        if not explicit_key.strip():
            raise HTTPException(status_code=401, detail="x-anthropic-api-key header is empty")
        await _record_credential_type("client_api_key")
        return AnthropicClient(api_key=explicit_key, base_url=base_url)

    # Passthrough: forward the request credential to Anthropic
    matches_proxy_key = secrets.compare_digest(token, api_key)
    use_passthrough = not matches_proxy_key or auth_mode == AuthMode.PASSTHROUGH
    if use_passthrough:
        if is_bearer:
            await _record_credential_type("oauth")
            return AnthropicClient(auth_token=token, base_url=base_url)
        await _record_credential_type("client_api_key")
        return AnthropicClient(api_key=token, base_url=base_url)

    # Proxy key fallback: use the server's configured client
    if base_client is None:
        raise HTTPException(
            status_code=500,
            detail="No Anthropic credentials available (set ANTHROPIC_API_KEY or use passthrough auth)",
        )
    await _record_credential_type("proxy_key_fallback")
    return base_client


# === ROUTES ===


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    _: str = Depends(verify_token),
    policy: OpenAIPolicyInterface = Depends(get_policy),
    llm_client: LLMClient = Depends(get_llm_client),
    emitter: EventEmitterProtocol = Depends(get_emitter),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
    usage_collector: UsageCollector | None = Depends(get_usage_collector),
):
    """OpenAI-compatible chat completions endpoint."""
    deps = get_dependencies(request)
    return await process_llm_request(
        request=request,
        policy=policy,
        llm_client=llm_client,
        emitter=emitter,
        db_pool=db_pool,
        enable_request_logging=deps.enable_request_logging,
        usage_collector=usage_collector,
    )


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


__all__ = ["LAST_CRED_TYPE_KEY", "router"]
