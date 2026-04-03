"""LLM gateway API routes with unified request processing."""

from __future__ import annotations

import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.credential_manager import AuthMode, CredentialManager, is_anthropic_api_key
from luthien_proxy.credentials import Credential, CredentialType
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


# === AUTH ===


async def get_request_credential(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> Credential:
    """Extract credential from request headers. Runs once per request.

    This is the bottom of the dependency chain — verify_token depends on it,
    and resolve_anthropic_client depends on verify_token. Each runs once
    because of the linear chain.
    """
    bearer_token = credentials.credentials if credentials else None
    api_key_header = request.headers.get("x-api-key")
    token = bearer_token or api_key_header
    if not token:
        raise HTTPException(status_code=401, detail="Missing API key")

    is_bearer = bearer_token is not None
    return Credential(
        value=token,
        credential_type=CredentialType.AUTH_TOKEN if is_bearer else CredentialType.API_KEY,
        platform="anthropic",
    )


async def verify_token(
    credential: Credential = Depends(get_request_credential),
    api_key: str | None = Depends(get_api_key),
    credential_manager: CredentialManager | None = Depends(get_credential_manager),
) -> Credential:
    """Validate the extracted credential. Returns it if valid."""
    token = credential.value
    is_bearer = credential.credential_type == CredentialType.AUTH_TOKEN

    if credential_manager is None:
        if api_key is not None and secrets.compare_digest(token, api_key):
            return credential
        raise HTTPException(status_code=401, detail="Invalid API key")

    auth_mode = credential_manager.config.auth_mode

    if auth_mode == AuthMode.PROXY_KEY:
        if api_key is not None and secrets.compare_digest(token, api_key):
            return credential
        raise HTTPException(status_code=401, detail="Invalid API key")

    if auth_mode == AuthMode.PASSTHROUGH:
        if credential_manager.config.validate_credentials:
            if not await credential_manager.validate_credential(token, is_bearer=is_bearer):
                raise HTTPException(status_code=401, detail="Invalid credential")
        return credential

    # BOTH mode: try proxy key first, fall through to passthrough
    if api_key is not None and secrets.compare_digest(token, api_key):
        return credential
    if credential_manager.config.validate_credentials:
        if not await credential_manager.validate_credential(token, is_bearer=is_bearer):
            raise HTTPException(status_code=401, detail="Invalid API key or credential")
    return credential


async def resolve_anthropic_client(
    request: Request,
    credential: Credential = Depends(verify_token),
    api_key: str | None = Depends(get_api_key),
    credential_manager: CredentialManager | None = Depends(get_credential_manager),
    base_client: AnthropicClient | None = Depends(get_anthropic_client),
) -> tuple[AnthropicClient, Credential]:
    """Build an AnthropicClient from the validated credential.

    Returns both the client and the forwarding credential. These may differ
    if x-anthropic-api-key is present (client authenticates with one key
    but forwards with another).
    """
    token = credential.value
    auth_mode = credential_manager.config.auth_mode if credential_manager else AuthMode.PROXY_KEY
    base_url = base_client._base_url if base_client else None

    async def _record_credential_type(cred_type: str) -> None:
        """Best-effort write of observed credential type for /health visibility."""
        if auth_mode == AuthMode.PROXY_KEY:
            return
        deps = getattr(request.app.state, "dependencies", None)
        if deps:
            deps.last_credential_info = {"type": cred_type, "timestamp": time.time()}

    # x-anthropic-api-key overrides the forwarding credential only.
    # The auth credential (from get_request_credential) was used for
    # validation. The override key is a separate identity for the backend.
    explicit_key = request.headers.get("x-anthropic-api-key")
    if explicit_key is not None:
        if not explicit_key.strip():
            raise HTTPException(status_code=401, detail="x-anthropic-api-key header is empty")
        await _record_credential_type("client_api_key")
        forwarding_cred = Credential(
            value=explicit_key,
            credential_type=CredentialType.API_KEY,
            platform="anthropic",
        )
        client = await anthropic_client_cache.get_client(explicit_key, auth_type="api_key", base_url=base_url)
        return client, forwarding_cred

    # Passthrough: forward the request credential to Anthropic
    matches_proxy_key = api_key is not None and secrets.compare_digest(token, api_key)
    use_passthrough = not matches_proxy_key or auth_mode == AuthMode.PASSTHROUGH
    if use_passthrough:
        # OAuth tokens arrive via Bearer header and are not Anthropic API keys.
        # In passthrough mode, x-api-key tokens that don't look like Anthropic
        # API keys (sk-ant-*) are also treated as OAuth tokens — Claude Code
        # may send OAuth tokens via either header depending on configuration.
        if not is_anthropic_api_key(token):
            is_bearer = credential.credential_type == CredentialType.AUTH_TOKEN
            cred_type = "oauth" if is_bearer else "oauth_via_api_key"
            await _record_credential_type(cred_type)
            auth_type = credential.credential_type.value
            client = await anthropic_client_cache.get_client(token, auth_type=auth_type, base_url=base_url)
            return client, credential
        await _record_credential_type("client_api_key")
        client = await anthropic_client_cache.get_client(token, auth_type="api_key", base_url=base_url)
        return client, credential

    # Proxy key fallback: use the server's configured client
    if base_client is None:
        raise HTTPException(
            status_code=500,
            detail="No Anthropic credentials available (set ANTHROPIC_API_KEY or use passthrough auth)",
        )
    await _record_credential_type("proxy_key_fallback")
    return base_client, credential


# === ROUTES ===


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    client_and_credential: tuple[AnthropicClient, Credential] = Depends(resolve_anthropic_client),
    anthropic_policy: AnthropicExecutionInterface = Depends(get_anthropic_policy),
    emitter: EventEmitterProtocol = Depends(get_emitter),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
    usage_collector: UsageCollector | None = Depends(get_usage_collector),
    credential_manager: CredentialManager | None = Depends(get_credential_manager),
):
    """Anthropic Messages API endpoint (native Anthropic path)."""
    anthropic_client, forwarding_credential = client_and_credential
    deps = get_dependencies(request)
    return await process_anthropic_request(
        request=request,
        policy=anthropic_policy,
        anthropic_client=anthropic_client,
        emitter=emitter,
        db_pool=db_pool,
        enable_request_logging=deps.enable_request_logging,
        usage_collector=usage_collector,
        user_credential=forwarding_credential,
        credential_manager=credential_manager,
    )


__all__ = ["router"]
