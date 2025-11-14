# ABOUTME: Admin API routes for policy management
# ABOUTME: Protected endpoints for listing, enabling, and querying policies

"""Admin API routes for policy management."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from luthien_proxy.policy_manager import (
    PolicyEnableResult,
    PolicyInfo,
    PolicyManager,
    _import_policy_class,
    _instantiate_policy,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBearer(auto_error=False)


# === Request/Response Models ===


class PolicyCreateRequest(BaseModel):
    """Request to create a policy instance."""

    name: str = Field(..., description="Unique name for this policy instance")
    policy_class_ref: str = Field(..., description="Full module path to policy class")
    config: dict[str, Any] = Field(default_factory=dict, description="Configuration for the policy")
    description: str | None = Field(None, description="Optional description of this policy instance")
    created_by: str = Field(default="api", description="Identifier of who created the policy")


class PolicyActivateRequest(BaseModel):
    """Request to activate a policy instance."""

    name: str = Field(..., description="Name of policy instance to activate")
    activated_by: str = Field(default="api", description="Identifier of who activated the policy")


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
    source_info: dict[str, Any]


class PolicySourceInfoResponse(BaseModel):
    """Response with policy source configuration info."""

    policy_source: str
    yaml_path: str
    supports_runtime_changes: bool
    persistence_target: str


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


class PolicyInstanceInfo(BaseModel):
    """Information about a saved policy instance."""

    id: int
    name: str
    policy_class_ref: str
    config: dict[str, Any]
    description: str | None
    created_at: str
    is_active: bool


class PolicyInstancesResponse(BaseModel):
    """Response with list of saved policy instances."""

    instances: list[PolicyInstanceInfo]


# === Auth ===


async def verify_admin_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Verify admin API key from Authorization header.

    Args:
        request: FastAPI request object
        credentials: HTTP Bearer credentials

    Returns:
        Admin API key if valid

    Raises:
        HTTPException: 403 if admin key is invalid or missing
    """
    admin_key = getattr(request.app.state, "admin_key", None)

    if not admin_key:
        raise HTTPException(
            status_code=500,
            detail="Admin authentication not configured (ADMIN_API_KEY not set)",
        )

    if credentials and credentials.credentials == admin_key:
        return credentials.credentials

    # Also check x-api-key header for convenience
    x_api_key = request.headers.get("x-api-key")
    if x_api_key and x_api_key == admin_key:
        return x_api_key

    raise HTTPException(
        status_code=403,
        detail="Admin access required. Provide valid admin API key via Authorization header.",
    )


# === Routes ===


@router.get("/policy/current", response_model=PolicyCurrentResponse)
async def get_current_policy(
    request: Request,
    _: str = Depends(verify_admin_token),
):
    """Get currently active policy with metadata.

    Returns information about the currently active policy including
    its configuration, when it was enabled, and source information.

    Requires admin authentication.
    """
    manager: PolicyManager = request.app.state.policy_manager

    try:
        policy_info: PolicyInfo = await manager.get_current_policy()
        return PolicyCurrentResponse(
            policy=policy_info.policy,
            class_ref=policy_info.class_ref,
            enabled_at=policy_info.enabled_at,
            enabled_by=policy_info.enabled_by,
            config=policy_info.config,
            source_info=policy_info.source_info,
        )
    except Exception as e:
        logger.error(f"Failed to get current policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get current policy: {e}")


@router.post("/policy/create", response_model=PolicyEnableResponse)
async def create_policy(
    request: Request,
    body: PolicyCreateRequest,
    _: str = Depends(verify_admin_token),
):
    """Create a named policy instance without activating it."""
    db_pool = request.app.state.db_pool

    try:
        # Validate policy can be instantiated
        policy_class = _import_policy_class(body.policy_class_ref)
        _instantiate_policy(policy_class, body.config)

        # Save to database
        pool = await db_pool.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO policy_config (name, policy_class_ref, config, description, enabled_by, is_active)
                VALUES ($1, $2, $3, $4, $5, false)
                RETURNING id
                """,
                body.name,
                body.policy_class_ref,
                json.dumps(body.config),
                body.description,
                body.created_by,
            )

        return PolicyEnableResponse(
            success=True,
            message=f"Created policy instance '{body.name}' (ID: {row['id']})",
            policy=body.name,
        )
    except Exception as e:
        logger.error(f"Failed to create policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/policy/activate", response_model=PolicyEnableResponse)
async def activate_policy(
    request: Request,
    body: PolicyActivateRequest,
    _: str = Depends(verify_admin_token),
):
    """Activate a saved policy instance by name."""
    manager: PolicyManager = request.app.state.policy_manager
    db_pool = request.app.state.db_pool

    try:
        # Load instance from database
        pool = await db_pool.get_pool()
        row = await pool.fetchrow(
            "SELECT policy_class_ref, config FROM policy_config WHERE name = $1",
            body.name,
        )
        if not row:
            raise HTTPException(status_code=404, detail=f"Policy instance '{body.name}' not found")

        # Activate it
        result: PolicyEnableResult = await manager.enable_policy(
            policy_class_ref=str(row["policy_class_ref"]),
            config=row["config"] if isinstance(row["config"], dict) else {},
            enabled_by=body.activated_by,
        )

        if not result.success:
            return PolicyEnableResponse(
                success=False,
                message=f"Failed to activate: {result.error}",
                error=result.error,
                troubleshooting=result.troubleshooting,
            )

        # Update app.state.policy
        request.app.state.policy = manager.current_policy

        # Mark as active in database
        await pool.execute("UPDATE policy_config SET is_active = false")
        await pool.execute(
            "UPDATE policy_config SET is_active = true WHERE name = $1",
            body.name,
        )

        return PolicyEnableResponse(
            success=True,
            message=f"Activated policy instance '{body.name}'",
            policy=result.policy,
            restart_duration_ms=result.restart_duration_ms,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to activate policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/policy/source-info", response_model=PolicySourceInfoResponse)
async def get_policy_source_info(
    request: Request,
    _: str = Depends(verify_admin_token),
):
    """Get information about policy source configuration.

    Returns details about how policies are loaded and persisted,
    including whether runtime changes are supported.

    Requires admin authentication.
    """
    manager: PolicyManager = request.app.state.policy_manager

    try:
        source_info = await manager.get_policy_source_info()
        return PolicySourceInfoResponse(**source_info)
    except Exception as e:
        logger.error(f"Failed to get policy source info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get source info: {e}")


@router.get("/policy/instances", response_model=PolicyInstancesResponse)
async def list_policy_instances(
    request: Request,
    _: str = Depends(verify_admin_token),
):
    """List all saved policy instances."""
    db_pool = request.app.state.db_pool
    pool = await db_pool.get_pool()

    rows = await pool.fetch(
        """
        SELECT id, name, policy_class_ref, config, description, created_at, is_active
        FROM policy_config
        ORDER BY created_at DESC
        """
    )

    instances = [
        PolicyInstanceInfo(
            id=row["id"],
            name=str(row["name"]) if row["name"] else f"policy-{row['id']}",
            policy_class_ref=str(row["policy_class_ref"]),
            config=row["config"] if isinstance(row["config"], dict) else {},
            description=str(row["description"]) if row["description"] else None,
            created_at=row["created_at"].isoformat()
            if hasattr(row["created_at"], "isoformat")
            else str(row["created_at"]),
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]

    return PolicyInstancesResponse(instances=instances)


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
    how to configure them before creating policy instances.

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
            name="SimplePolicy",
            class_ref="luthien_proxy.policies.simple_policy:SimplePolicy",
            description="LLM-based tool call judge that evaluates safety before allowing tool execution",
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
            },
            example_config={
                "judge_model": "claude-3-5-sonnet-20241022",
                "judge_temperature": 0.0,
                "allowed_tools": ["read_file", "write_file"],
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
