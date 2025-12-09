"""Shared authentication utilities for admin and debug endpoints."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.dependencies import get_admin_key

security = HTTPBearer(auto_error=False)


async def verify_admin_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    admin_key: str | None = Depends(get_admin_key),
) -> str:
    """Verify admin API key from Authorization header.

    Accepts authentication via:
    - Bearer token in Authorization header
    - x-api-key header

    Uses constant-time comparison to prevent timing attacks.

    Args:
        request: FastAPI request object
        credentials: HTTP Bearer credentials
        admin_key: Admin API key from dependencies

    Returns:
        Admin API key if valid

    Raises:
        HTTPException: 500 if admin key not configured, 403 if invalid or missing
    """
    if not admin_key:
        raise HTTPException(
            status_code=500,
            detail="Admin authentication not configured (ADMIN_API_KEY not set)",
        )

    # Use constant-time comparison to prevent timing attacks
    if credentials and secrets.compare_digest(credentials.credentials, admin_key):
        return credentials.credentials

    # Also check x-api-key header for convenience
    x_api_key = request.headers.get("x-api-key")
    if x_api_key and secrets.compare_digest(x_api_key, admin_key):
        return x_api_key

    raise HTTPException(
        status_code=403,
        detail="Admin access required. Provide valid admin API key via Authorization header.",
    )


__all__ = ["verify_admin_token", "security"]
