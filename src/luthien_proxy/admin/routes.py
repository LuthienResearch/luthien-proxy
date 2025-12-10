"""Admin API routes for policy management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from luthien_proxy.auth import verify_admin_token
from luthien_proxy.dependencies import get_policy_manager
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
    # Hardcoded list of available policies
    # TODO: Consider dynamic discovery via importlib/pkgutil
    policies = [
        PolicyClassInfo(
            name="NoOpPolicy",
            class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            description="Pass-through policy that makes no modifications to requests or responses",
            config_schema={},
            example_config={},
        ),
        PolicyClassInfo(
            name="AllCapsPolicy",
            class_ref="luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
            description="Converts all response content to uppercase (for testing/demonstration)",
            config_schema={},
            example_config={},
        ),
        PolicyClassInfo(
            name="DebugLoggingPolicy",
            class_ref="luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy",
            description="Logs all requests and responses for debugging purposes",
            config_schema={},
            example_config={},
        ),
        PolicyClassInfo(
            name="SimpleJudgePolicy",
            class_ref="luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
            description="Easy-to-customize LLM judge for content and tool calls - just define RULES in a subclass",
            config_schema={
                "judge_model": {
                    "type": "string",
                    "description": "Model to use for judging",
                    "default": "claude-3-5-sonnet-20241022",
                },
                "judge_temperature": {
                    "type": "number",
                    "description": "Temperature for judge model",
                    "default": 0.0,
                    "minimum": 0.0,
                    "maximum": 2.0,
                },
                "block_threshold": {
                    "type": "number",
                    "description": "Confidence threshold for blocking (0-1)",
                    "default": 0.7,
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            example_config={
                "judge_model": "claude-3-5-sonnet-20241022",
                "judge_temperature": 0.0,
                "block_threshold": 0.7,
            },
        ),
        PolicyClassInfo(
            name="ToolCallJudgePolicy",
            class_ref="luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
            description="Advanced LLM-based tool call judge with detailed safety evaluation",
            config_schema={
                "judge_model": {
                    "type": "string",
                    "description": "Model to use for judging tool calls",
                    "default": "claude-3-5-sonnet-20241022",
                },
                "judge_temperature": {
                    "type": "number",
                    "description": "Temperature for judge model",
                    "default": 0.0,
                    "minimum": 0.0,
                    "maximum": 2.0,
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tool names that are allowed (empty = all allowed)",
                    "default": [],
                },
                "block_threshold": {
                    "type": "number",
                    "description": "Confidence threshold for blocking (0-1)",
                    "default": 0.7,
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            example_config={
                "judge_model": "claude-3-5-sonnet-20241022",
                "judge_temperature": 0.0,
                "allowed_tools": ["read_file", "list_directory"],
                "block_threshold": 0.7,
            },
        ),
    ]

    return PolicyListResponse(policies=policies)


__all__ = ["router"]
