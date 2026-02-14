"""LLM gateway API routes with unified request processing."""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.credential_manager import AuthMode, CredentialManager
from luthien_proxy.dependencies import (
    get_anthropic_client,
    get_anthropic_policy,
    get_api_key,
    get_credential_manager,
    get_emitter,
    get_llm_client,
    get_policy,
)
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.client import LLMClient
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.pipeline import ClientFormat, process_anthropic_request, process_llm_request
from luthien_proxy.policy_core.anthropic_interface import AnthropicPolicyInterface
from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface
from luthien_proxy.utils.constants import API_KEY_HASH_LENGTH

router = APIRouter(tags=["gateway"])
security = HTTPBearer(auto_error=False)


# === AUTH ===
async def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    api_key: str = Depends(get_api_key),
    credential_manager: CredentialManager | None = Depends(get_credential_manager),
) -> str:
    """Verify API key, supporting proxy_key, passthrough, and both auth modes."""
    token = None
    if credentials:
        token = credentials.credentials
    if not token:
        token = request.headers.get("x-api-key")
    if not token:
        raise HTTPException(status_code=401, detail="Missing API key")

    auth_mode = credential_manager.config.auth_mode if credential_manager else AuthMode.PROXY_KEY

    if auth_mode == AuthMode.PROXY_KEY:
        if secrets.compare_digest(token, api_key):
            return token
        raise HTTPException(status_code=401, detail="Invalid API key")

    if auth_mode == AuthMode.PASSTHROUGH:
        if not credential_manager:
            raise HTTPException(status_code=500, detail="Passthrough auth not available")
        if credential_manager.config.validate_credentials:
            is_valid = await credential_manager.validate_credential(token)
            if not is_valid:
                raise HTTPException(status_code=401, detail="Invalid credential")
        request.state.passthrough_api_key = token
        return token

    # both mode: try proxy key first, fall through to passthrough
    assert auth_mode == AuthMode.BOTH
    if secrets.compare_digest(token, api_key):
        return token
    if not credential_manager:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if credential_manager.config.validate_credentials:
        is_valid = await credential_manager.validate_credential(token)
        if not is_valid:
            raise HTTPException(status_code=401, detail="Invalid API key or credential")
    request.state.passthrough_api_key = token
    return token


def hash_api_key(key: str) -> str:
    """Hash API key for logging."""
    return hashlib.sha256(key.encode()).hexdigest()[:API_KEY_HASH_LENGTH]


# === ROUTES ===


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    _: str = Depends(verify_token),
    policy: OpenAIPolicyInterface = Depends(get_policy),
    llm_client: LLMClient = Depends(get_llm_client),
    emitter: EventEmitterProtocol = Depends(get_emitter),
):
    """OpenAI-compatible chat completions endpoint."""
    return await process_llm_request(
        request=request,
        client_format=ClientFormat.OPENAI,
        policy=policy,
        llm_client=llm_client,
        emitter=emitter,
    )


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    _: str = Depends(verify_token),
    anthropic_policy: AnthropicPolicyInterface = Depends(get_anthropic_policy),
    anthropic_client: AnthropicClient = Depends(get_anthropic_client),
    emitter: EventEmitterProtocol = Depends(get_emitter),
):
    """Anthropic Messages API endpoint (native Anthropic path)."""
    # Explicit x-anthropic-api-key header takes precedence
    client_api_key = request.headers.get("x-anthropic-api-key")
    if client_api_key is not None:
        if not client_api_key.strip():
            raise HTTPException(status_code=401, detail="x-anthropic-api-key header is empty")
        anthropic_client = anthropic_client.with_api_key(client_api_key)
    elif hasattr(request.state, "passthrough_api_key"):
        anthropic_client = anthropic_client.with_api_key(request.state.passthrough_api_key)

    return await process_anthropic_request(
        request=request,
        policy=anthropic_policy,
        anthropic_client=anthropic_client,
        emitter=emitter,
    )


__all__ = ["router"]
