"""Admin API routes for policy management."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import litellm
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from luthien_proxy.admin.policy_discovery import discover_policies
from luthien_proxy.auth import verify_admin_token
from luthien_proxy.dependencies import get_policy_manager
from luthien_proxy.policy_manager import (
    PolicyEnableResult,
    PolicyInfo,
    PolicyManager,
)
from luthien_proxy.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# === Request/Response Models ===


class PolicySetRequest(BaseModel):
    """Request to set the active policy."""

    policy_class_ref: str = Field(..., description="Full module path to policy class")
    config: dict[str, Any] = Field(default_factory=dict, description="Configuration for the policy")
    enabled_by: str = Field(default="api", description="Identifier of who enabled the policy")


class PolicyEnableResponse(BaseModel):
    """Response from enabling a policy."""

    success: bool
    message: str
    policy: str | None = None
    restart_duration_ms: int | None = None
    error: str | None = None
    troubleshooting: list[str] | None = None


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


class ChatResponse(BaseModel):
    """Response from test chat."""

    success: bool
    content: str | None = None
    error: str | None = None
    model: str | None = None
    usage: dict[str, Any] | None = None


def get_available_models() -> list[str]:
    """Get available models from litellm.

    Returns a curated list of chat completion models from OpenAI and Anthropic.
    """
    models: list[str] = []

    # Get OpenAI chat models
    if hasattr(litellm, "open_ai_chat_completion_models"):
        openai_models = [
            m
            for m in litellm.open_ai_chat_completion_models
            if m.startswith(("gpt-", "o1", "o3", "chatgpt-"))
            and not m.startswith("ft:")
            and "audio" not in m
            and "realtime" not in m
        ]
        models.extend(sorted(openai_models, reverse=True))

    # Get Anthropic models
    if hasattr(litellm, "anthropic_models"):
        anthropic_models = [m for m in litellm.anthropic_models if "claude" in m.lower()]
        models.extend(sorted(anthropic_models, reverse=True))

    return models


# === Routes ===


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
        logger.error(f"Failed to get current policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get current policy: {e}")


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
        result: PolicyEnableResult = await manager.enable_policy(
            policy_class_ref=body.policy_class_ref,
            config=body.config,
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
    # Note: Policy discovery is performed on every request. Since the policy set
    # is static at runtime, this could be cached if performance becomes a concern.
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

    Returns a list of models available via litellm (OpenAI and Anthropic).
    Requires admin authentication.
    """
    return {"models": get_available_models()}


@router.post("/test/chat", response_model=ChatResponse)
async def send_chat(
    body: ChatRequest,
    request: Request,
    _: str = Depends(verify_admin_token),
):
    """Send a test message through the proxy with the active policy.

    This endpoint acts as an injection point, forwarding the request to
    /v1/chat/completions with the server's PROXY_API_KEY. This ensures
    the test goes through the full policy pipeline (on_request, LLM call,
    on_response) exactly as real client requests do.

    Requires admin authentication.
    """
    settings = get_settings()
    if not settings.proxy_api_key:
        return ChatResponse(
            success=False,
            error="PROXY_API_KEY not configured on server",
            model=body.model,
        )

    # Build the base URL from the incoming request
    # Note: This creates a self-calling HTTP request back to the same server.
    # This ensures the test goes through the full policy pipeline but may have
    # issues behind reverse proxies or with certain async configurations.
    base_url = str(request.base_url).rstrip("/")

    # Build OpenAI-format request payload
    payload = {
        "model": body.model,
        "messages": [{"role": "user", "content": body.message}],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.proxy_api_key}"},
            )

        if response.status_code != 200:
            error_detail = response.text
            try:
                error_json = response.json()
                error_detail = error_json.get("detail", error_detail)
            except Exception:
                pass
            return ChatResponse(
                success=False,
                error=f"Proxy returned {response.status_code}: {error_detail}",
                model=body.model,
            )

        data = response.json()

        # Extract content from response
        content = None
        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content")

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
        logger.error(f"Test chat failed: {e}", exc_info=True)
        return ChatResponse(
            success=False,
            error=str(e),
            model=body.model,
        )


__all__ = ["router"]
