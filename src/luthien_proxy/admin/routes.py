"""Admin API routes for policy management."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import litellm
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from luthien_proxy.admin.policy_discovery import discover_policies
from luthien_proxy.auth import verify_admin_token
from luthien_proxy.dependencies import get_emitter, get_llm_client, get_policy, get_policy_manager
from luthien_proxy.llm.client import LLMClient
from luthien_proxy.llm.types import Request as LLMRequest
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.policy_manager import (
    PolicyEnableResult,
    PolicyInfo,
    PolicyManager,
)

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


class TestChatRequest(BaseModel):
    """Request for testing chat through the proxy."""

    model: str = Field(..., description="Model to use (e.g., 'gpt-4o', 'claude-3-5-sonnet-20241022')")
    message: str = Field(..., description="Message to send")
    stream: bool = Field(default=False, description="Whether to stream the response")


class TestChatResponse(BaseModel):
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


@router.post("/test/chat", response_model=TestChatResponse)
async def test_chat(
    body: TestChatRequest,
    _: str = Depends(verify_admin_token),
    policy: PolicyProtocol = Depends(get_policy),
    llm_client: LLMClient = Depends(get_llm_client),
    emitter: EventEmitterProtocol = Depends(get_emitter),
):
    """Send a test message through the proxy with the active policy.

    This endpoint allows testing the current policy configuration
    by sending a message and seeing the response. The message goes
    through the full policy pipeline.

    Requires admin authentication.
    """
    transaction_id = f"test-{uuid.uuid4().hex[:12]}"

    try:
        # Build OpenAI-format request
        messages: list[Any] = [{"role": "user", "content": body.message}]
        request = LLMRequest(model=body.model, messages=messages, stream=False)

        # Create policy context
        ctx = PolicyContext(
            transaction_id=transaction_id,
            request=request,
            emitter=emitter,
        )

        # Apply policy on request
        modified_request = await policy.on_request(request, ctx)

        # Call LLM
        response = await llm_client.complete(modified_request)

        # Extract content from response (using getattr for type safety)
        content = None
        choices = getattr(response, "choices", None)
        if choices:
            choice = choices[0]
            msg = getattr(choice, "message", None)
            if msg:
                content = getattr(msg, "content", None)

        # Extract usage
        usage = None
        usage_obj = getattr(response, "usage", None)
        if usage_obj:
            usage = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
                "total_tokens": getattr(usage_obj, "total_tokens", 0),
            }

        return TestChatResponse(
            success=True,
            content=content,
            model=body.model,
            usage=usage,
        )
    except Exception as e:
        logger.error(f"Test chat failed: {e}", exc_info=True)
        return TestChatResponse(
            success=False,
            error=str(e),
            model=body.model,
        )


__all__ = ["router"]
