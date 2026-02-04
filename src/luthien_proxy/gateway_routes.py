"""LLM gateway API routes with unified request processing."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.dependencies import (
    get_anthropic_client,
    get_anthropic_policy,
    get_api_key,
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
def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    api_key: str = Depends(get_api_key),
) -> str:
    """Verify API key from either Authorization header or x-api-key header."""
    if credentials and credentials.credentials == api_key:
        return credentials.credentials

    x_api_key = request.headers.get("x-api-key")
    if x_api_key and x_api_key == api_key:
        return x_api_key

    raise HTTPException(status_code=401, detail="Invalid API key")


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
    return await process_anthropic_request(
        request=request,
        policy=anthropic_policy,
        anthropic_client=anthropic_client,
        emitter=emitter,
    )


__all__ = ["router"]
