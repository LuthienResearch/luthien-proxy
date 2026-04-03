"""Admin API routes for policy management."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import litellm
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from luthien_proxy.admin.policy_discovery import discover_policies, validate_policy_config
from luthien_proxy.auth import verify_admin_token
from luthien_proxy.config import _import_policy_class
from luthien_proxy.credential_manager import AuthConfig, AuthMode, CredentialError, CredentialManager
from luthien_proxy.credentials import Credential, CredentialType
from luthien_proxy.dependencies import get_db_pool, get_policy_manager, require_credential_manager
from luthien_proxy.policy_manager import (
    PolicyEnableResult,
    PolicyInfo,
    PolicyManager,
)
from luthien_proxy.settings import client_error_detail, get_settings
from luthien_proxy.usage_telemetry.config import resolve_telemetry_config
from luthien_proxy.utils import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class PolicySetRequest(BaseModel):
    """Request to set the active policy."""

    policy_class_ref: str = Field(..., description="Full module path to policy class")
    config: dict[str, Any] = Field(default_factory=dict, description="Configuration for the policy")
    enabled_by: str = Field(default="api", description="Identifier of who enabled the policy")


class PolicyEnableResponse(BaseModel):
    """Response from enabling a policy."""

    success: bool
    message: str | None = None
    policy: str | None = None
    restart_duration_ms: int | None = None
    error: str | None = None
    troubleshooting: list[str] | None = None
    validation_errors: list[dict] | None = None


class PolicyCurrentResponse(BaseModel):
    """Response with current policy information."""

    policy: str
    class_ref: str
    enabled_at: str | None
    enabled_by: str | None
    config: dict[str, Any]


class PolicyClassInfo(BaseModel):
    """Information about an available policy class."""

    name: str = Field(..., description="Policy class name (e.g., 'NoOpPolicy')")
    class_ref: str = Field(..., description="Full module path to policy class")
    description: str = Field(..., description="Description of what the policy does")
    config_schema: dict[str, Any] = Field(default_factory=dict, description="Schema for config parameters")
    example_config: dict[str, Any] = Field(default_factory=dict, description="Example configuration")


class PolicyListResponse(BaseModel):
    """Response with list of available policy classes."""

    policies: list[PolicyClassInfo]


class ChatRequest(BaseModel):
    """Request for testing chat through the proxy."""

    model: str = Field(..., description="Model to use (e.g., 'gpt-4o', 'claude-3-5-sonnet-20241022')")
    message: str = Field(..., description="Message to send")
    stream: bool = Field(default=False, description="Whether to stream the response")
    use_mock: bool = Field(
        default=False,
        description="Use a mock LLM response so no upstream API key is required. "
        "The policy pipeline still runs on both the request and the mock response. "
        "Set to True to skip the real LLM call (useful when no server-side LLM credentials are configured).",
    )
    api_key: str | None = Field(
        default=None,
        description="Optional API key to use for this test request. "
        "Overrides the server's proxy key as the credential sent to the gateway.",
    )


class ChatResponse(BaseModel):
    """Response from test chat."""

    success: bool
    content: str | None = None
    error: str | None = None
    model: str | None = None
    usage: dict[str, Any] | None = None


class AuthConfigResponse(BaseModel):
    """Response with current auth configuration."""

    auth_mode: str
    validate_credentials: bool
    valid_cache_ttl_seconds: int
    invalid_cache_ttl_seconds: int
    updated_at: str | None = None
    updated_by: str | None = None


class AuthConfigUpdateRequest(BaseModel):
    """Request to update auth configuration."""

    auth_mode: str | None = Field(default=None, description="Auth mode: proxy_key, passthrough, or both")
    validate_credentials: bool | None = Field(default=None)
    valid_cache_ttl_seconds: int | None = Field(default=None, gt=0)
    invalid_cache_ttl_seconds: int | None = Field(default=None, gt=0)


class CachedCredentialResponse(BaseModel):
    """A cached credential entry."""

    key_hash: str
    valid: bool
    validated_at: float
    last_used_at: float


class CachedCredentialsListResponse(BaseModel):
    """Response with list of cached credentials."""

    credentials: list[CachedCredentialResponse]
    count: int


def get_available_models() -> list[str]:
    """Get available Anthropic models for testing.

    Returns a list of Claude models available via litellm.
    """
    anthropic_models = [m for m in litellm.anthropic_models if "claude" in m.lower()]
    return sorted(anthropic_models, reverse=True)


@router.get("/policy/current", response_model=PolicyCurrentResponse)
async def get_current_policy(
    _: str = Depends(verify_admin_token),
    manager: PolicyManager = Depends(get_policy_manager),
):
    """Get currently active policy with metadata.

    Returns information about the currently active policy including
    its configuration and when it was enabled.

    Requires admin authentication.
    """
    try:
        policy_info: PolicyInfo = await manager.get_current_policy()
        return PolicyCurrentResponse(
            policy=policy_info.policy,
            class_ref=policy_info.class_ref,
            enabled_at=policy_info.enabled_at,
            enabled_by=policy_info.enabled_by,
            config=policy_info.config,
        )
    except Exception as e:
        logger.error(f"Failed to get current policy: {repr(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=client_error_detail(f"Failed to get current policy: {e}"))


@router.post("/policy/set", response_model=PolicyEnableResponse)
async def set_policy(
    body: PolicySetRequest,
    _: str = Depends(verify_admin_token),
    manager: PolicyManager = Depends(get_policy_manager),
):
    """Set the active policy.

    This is the primary endpoint for changing the active policy.
    The policy is validated, activated in memory, and persisted to the database.

    Requires admin authentication.
    """
    try:
        # Import policy class and validate config before enabling
        policy_class = _import_policy_class(body.policy_class_ref)
        validated_config = validate_policy_config(policy_class, body.config or {})

        result: PolicyEnableResult = await manager.enable_policy(
            policy_class_ref=body.policy_class_ref,
            config=validated_config,
            enabled_by=body.enabled_by,
        )

        if not result.success:
            return PolicyEnableResponse(
                success=False,
                message=f"Failed to set policy: {result.error}",
                error=result.error,
                troubleshooting=result.troubleshooting,
            )

        return PolicyEnableResponse(
            success=True,
            message=f"Policy set to {body.policy_class_ref}",
            policy=result.policy,
            restart_duration_ms=result.restart_duration_ms,
        )
    except ValidationError as e:
        return PolicyEnableResponse(
            success=False,
            error="Validation error",
            troubleshooting=[f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()],
            validation_errors=[dict(err) for err in e.errors()],
        )
    except ValueError as e:
        logger.warning(f"Policy validation error: {repr(e)}")
        return PolicyEnableResponse(
            success=False,
            error="Validation error",
            troubleshooting=[client_error_detail(str(e), "Check the policy configuration values and try again.")],
        )
    except (ImportError, AttributeError, TypeError) as e:
        logger.warning(f"Policy load error: {repr(e)}")
        return PolicyEnableResponse(
            success=False,
            error=client_error_detail(str(e), "Failed to load policy class."),
            troubleshooting=[
                "Check that the policy class reference is correct",
                "Verify the policy module exists and is importable",
                "Example format: 'luthien_proxy.policies.all_caps_policy:AllCapsPolicy'",
            ],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set policy: {repr(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=client_error_detail(str(e)))


@router.get("/policy/list", response_model=PolicyListResponse)
async def list_available_policies(
    _: str = Depends(verify_admin_token),
):
    """List available policy classes with metadata.

    Returns information about all available policy classes including:
    - Policy name and class reference
    - Description of what the policy does
    - Configuration schema (parameter names, types, defaults)
    - Example configuration

    This endpoint helps users discover what policies are available and
    how to configure them.

    Requires admin authentication.
    """
    discovered = discover_policies()
    policies = [
        PolicyClassInfo(
            name=p["name"],
            class_ref=p["class_ref"],
            description=p["description"],
            config_schema=p["config_schema"],
            example_config=p["example_config"],
        )
        for p in discovered
    ]
    return PolicyListResponse(policies=policies)


@router.get("/models")
async def list_models(
    _: str = Depends(verify_admin_token),
):
    """List available models for testing.

    Returns a list of Anthropic Claude models available via litellm.
    Requires admin authentication.
    """
    return {"models": get_available_models()}


@router.post("/test/chat", response_model=ChatResponse)
async def send_chat(
    body: ChatRequest,
    _: str = Depends(verify_admin_token),
):
    """Send a test message through the proxy with the active policy.

    Forwards the request to the gateway's /v1/messages endpoint using either
    the server's PROXY_API_KEY or a custom API key. In mock mode, returns the
    user's message as an echo without calling the LLM or running the policy
    pipeline (useful for quick checks without API credits).

    Requires admin authentication.
    """
    settings = get_settings()

    # Mock mode: echo the user's message back as if the LLM responded with it.
    # Useful for testing how a policy transforms text without spending API credits.
    if body.use_mock:
        return ChatResponse(
            success=True,
            content=body.message,
            model=body.model,
        )

    # Determine which API key to use: custom key takes precedence over server proxy key
    test_api_key = settings.proxy_api_key
    if body.api_key is not None and body.api_key.strip():
        test_api_key = body.api_key.strip()

    if not test_api_key:
        return ChatResponse(
            success=False,
            error="No API key available — set PROXY_API_KEY on the server or provide a custom key",
            model=body.model,
        )

    # Use the internal self-URL so this works both on the host and inside Docker,
    # where the external port mapping (e.g. 8001) is not reachable from the container.
    base_url = f"http://localhost:{settings.gateway_port}"

    # Build Anthropic-format request payload
    payload: dict[str, Any] = {
        "model": body.model,
        "messages": [{"role": "user", "content": body.message}],
        "max_tokens": 1024,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.post(
                f"{base_url}/v1/messages",
                json=payload,
                headers={"x-api-key": test_api_key},
            )

        if response.status_code != 200:
            error_detail = response.text
            try:
                error_json = response.json()
                error_detail = error_json.get("detail", error_detail)
            except ValueError as e:
                logger.debug(f"Could not parse error response as JSON: {repr(e)}")
            return ChatResponse(
                success=False,
                error=f"Proxy returned {response.status_code}: {error_detail}",
                model=body.model,
            )

        data = response.json()

        # Extract content from Anthropic response format
        content = None
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                content = (content or "") + block.get("text", "")

        # Extract usage
        usage = data.get("usage")

        return ChatResponse(
            success=True,
            content=content,
            model=body.model,
            usage=usage,
        )
    except httpx.TimeoutException:
        return ChatResponse(
            success=False,
            error="Request timed out (120s limit)",
            model=body.model,
        )
    except Exception as e:
        logger.error(f"Test chat failed: {repr(e)}", exc_info=True)
        return ChatResponse(
            success=False,
            error=client_error_detail(str(e), "An unexpected error occurred"),
            model=body.model,
        )


def _config_to_response(config: AuthConfig) -> AuthConfigResponse:
    return AuthConfigResponse(
        auth_mode=config.auth_mode.value,
        validate_credentials=config.validate_credentials,
        valid_cache_ttl_seconds=config.valid_cache_ttl_seconds,
        invalid_cache_ttl_seconds=config.invalid_cache_ttl_seconds,
        updated_at=config.updated_at,
        updated_by=config.updated_by,
    )


@router.get("/auth/config", response_model=AuthConfigResponse)
async def get_auth_config(
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Get current authentication configuration."""
    return _config_to_response(credential_manager.config)


@router.post("/auth/config", response_model=AuthConfigResponse)
async def update_auth_config(
    body: AuthConfigUpdateRequest,
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Update authentication configuration."""
    if body.auth_mode is not None:
        try:
            AuthMode(body.auth_mode)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid auth_mode: {body.auth_mode}. Must be one of: proxy_key, passthrough, both",
            )

    config = await credential_manager.update_config(
        auth_mode=body.auth_mode,
        validate_credentials=body.validate_credentials,
        valid_cache_ttl_seconds=body.valid_cache_ttl_seconds,
        invalid_cache_ttl_seconds=body.invalid_cache_ttl_seconds,
        updated_by="admin-api",
    )
    return _config_to_response(config)


@router.get("/auth/credentials", response_model=CachedCredentialsListResponse)
async def list_cached_credentials(
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """List all cached credentials (hashes and metadata only)."""
    cached = await credential_manager.list_cached()
    credentials = [
        CachedCredentialResponse(
            key_hash=c.key_hash,
            valid=c.valid,
            validated_at=c.validated_at,
            last_used_at=c.last_used_at,
        )
        for c in cached
    ]
    return CachedCredentialsListResponse(credentials=credentials, count=len(credentials))


@router.delete("/auth/credentials/{key_hash}")
async def invalidate_credential(
    key_hash: str,
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Invalidate a single cached credential by its hash."""
    found = await credential_manager.invalidate_credential(key_hash)
    if not found:
        raise HTTPException(status_code=404, detail="Credential not found in cache")
    return {"success": True, "message": "Credential invalidated"}


@router.delete("/auth/credentials")
async def invalidate_all_credentials(
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Invalidate all cached credentials."""
    count = await credential_manager.invalidate_all()
    return {"success": True, "count": count, "message": f"Invalidated {count} cached credentials"}


# === Server Credentials ===


class ServerCredentialRequest(BaseModel):
    """Request to create/update a server credential."""

    name: str = Field(..., description="Unique name for the credential (e.g. 'judge-api-key')")
    value: str = Field(..., description="The credential value (API key or OAuth token)")
    credential_type: str = Field(default="api_key", description="'api_key' or 'auth_token'")
    platform: str = Field(default="anthropic", description="Provider platform")
    platform_url: str | None = Field(default=None, description="Custom base URL")


@router.post("/credentials")
async def put_server_credential(
    body: ServerCredentialRequest,
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Create or update a server credential."""
    try:
        cred_type = CredentialType(body.credential_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid credential_type: {body.credential_type}. Must be 'api_key' or 'auth_token'",
        )

    credential = Credential(
        value=body.value,
        credential_type=cred_type,
        platform=body.platform,
        platform_url=body.platform_url,
    )
    try:
        await credential_manager.put_server_credential(body.name, credential)
    except CredentialError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"success": True, "name": body.name}


@router.get("/credentials")
async def list_server_credentials(
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """List server credential names (no values exposed)."""
    names = await credential_manager.list_server_credentials()
    return {"credentials": names, "count": len(names)}


@router.delete("/credentials/{name}")
async def delete_server_credential(
    name: str,
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Delete a server credential."""
    try:
        deleted = await credential_manager.delete_server_credential(name)
    except CredentialError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Server credential '{name}' not found")
    return {"success": True, "name": name}


# === Telemetry ===


class TelemetryConfigResponse(BaseModel):
    """Response with current telemetry configuration."""

    enabled: bool
    deployment_id: str
    env_override: bool
    user_configured: bool


class TelemetryConfigUpdateRequest(BaseModel):
    """Request to update telemetry enabled state."""

    enabled: bool


@router.get("/telemetry")
async def get_telemetry_config(
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """Get current telemetry configuration."""
    settings = get_settings()
    config = await resolve_telemetry_config(db_pool=db_pool, env_value=settings.usage_telemetry)
    return TelemetryConfigResponse(
        enabled=config.enabled,
        deployment_id=config.deployment_id,
        env_override=settings.usage_telemetry is not None,
        user_configured=config.user_configured,
    )


@router.put("/telemetry")
async def update_telemetry_config(
    body: TelemetryConfigUpdateRequest,
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """Update telemetry enabled state (stored in DB)."""
    settings = get_settings()
    if settings.usage_telemetry is not None:
        raise HTTPException(
            status_code=409,
            detail="USAGE_TELEMETRY env var is set — DB config cannot override it",
        )
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    pool = await db_pool.get_pool()
    await pool.execute(
        "UPDATE telemetry_config SET enabled = $1, updated_at = NOW(), updated_by = 'admin-api' WHERE id = 1",
        body.enabled,
    )
    return {"success": True, "enabled": body.enabled}


# === Gateway Settings ===


class GatewaySettingsResponse(BaseModel):
    """Response with current gateway settings."""

    inject_policy_context: bool
    dogfood_mode: bool


class GatewaySettingsUpdateRequest(BaseModel):
    """Request to update gateway settings."""

    inject_policy_context: bool | None = None
    dogfood_mode: bool | None = None


@router.get("/gateway/settings", response_model=GatewaySettingsResponse)
async def get_gateway_settings(
    _: str = Depends(verify_admin_token),
):
    """Get current gateway settings."""
    settings = get_settings()
    return GatewaySettingsResponse(
        inject_policy_context=settings.inject_policy_context,
        dogfood_mode=settings.dogfood_mode,
    )


@router.put("/gateway/settings", response_model=GatewaySettingsResponse)
async def update_gateway_settings(
    body: GatewaySettingsUpdateRequest,
    _: str = Depends(verify_admin_token),
):
    """Update gateway settings at runtime.

    These settings take effect immediately for new requests.
    Env var values serve as defaults; runtime updates override them
    until the gateway restarts.
    """
    settings = get_settings()
    if body.inject_policy_context is not None:
        settings.inject_policy_context = body.inject_policy_context
    if body.dogfood_mode is not None:
        settings.dogfood_mode = body.dogfood_mode
    return GatewaySettingsResponse(
        inject_policy_context=settings.inject_policy_context,
        dogfood_mode=settings.dogfood_mode,
    )


__all__ = ["router"]
