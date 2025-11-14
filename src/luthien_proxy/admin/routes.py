# ABOUTME: Admin API routes for policy management
# ABOUTME: Protected endpoints for listing, enabling, and querying policies

"""Admin API routes for policy management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from luthien_proxy.policy_manager import PolicyEnableResult, PolicyInfo, PolicyManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBearer(auto_error=False)


# === Request/Response Models ===


class PolicyEnableRequest(BaseModel):
    """Request to enable a policy."""

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
    source_info: dict[str, Any]


class PolicySourceInfoResponse(BaseModel):
    """Response with policy source configuration info."""

    policy_source: str
    yaml_path: str
    supports_runtime_changes: bool
    persistence_target: str


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


@router.post("/policy/enable", response_model=PolicyEnableResponse)
async def enable_policy(
    request: Request,
    body: PolicyEnableRequest,
    _: str = Depends(verify_admin_token),
):
    """Enable a new policy.

    Validates the policy, persists the configuration (to database or file
    depending on POLICY_SOURCE), and hot-swaps the policy at runtime.

    Requires admin authentication.

    Args:
        request: FastAPI request object
        body: Policy enable request with class reference and config

    Returns:
        Success status with timing information or error details
    """
    manager: PolicyManager = request.app.state.policy_manager

    logger.info(
        f"Policy enable request: {body.policy_class_ref} "
        f"by {body.enabled_by} "
        f"with config keys: {list(body.config.keys())}"
    )

    try:
        result: PolicyEnableResult = await manager.enable_policy(
            policy_class_ref=body.policy_class_ref,
            config=body.config,
            enabled_by=body.enabled_by,
        )

        if not result.success:
            return PolicyEnableResponse(
                success=False,
                message=f"Failed to enable policy: {result.error}",
                error=result.error,
                troubleshooting=result.troubleshooting,
            )

        return PolicyEnableResponse(
            success=True,
            message=f"Successfully enabled {result.policy}",
            policy=result.policy,
            restart_duration_ms=result.restart_duration_ms,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error enabling policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


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


__all__ = ["router"]
