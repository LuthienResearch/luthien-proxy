"""Shared authentication utilities for admin and debug endpoints.

Supports three authentication methods:
1. Session cookie (for browser access after login)
2. Bearer token in Authorization header (for API access)
3. x-api-key header (for API access)

Localhost bypass: when LOCALHOST_AUTH_BYPASS=true (default), requests from
127.0.0.1 or ::1 skip auth for UI routes. Admin API routes (/api/admin/*)
always require auth regardless of this setting.
"""

from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.dependencies import get_admin_key
from luthien_proxy.session import get_session_user
from luthien_proxy.settings import get_settings

security = HTTPBearer(auto_error=False)

_LOCALHOST_IPS = ("127.0.0.1", "::1")
_ADMIN_API_PREFIX = "/api/admin/"


def is_localhost_request(request: Request) -> bool:
    """Check whether the request originates from a loopback address."""
    client = request.client
    if client is None:
        return False
    return client.host in _LOCALHOST_IPS


def _should_bypass_auth(request: Request) -> bool:
    """Return True if auth can be skipped for this request.

    Bypasses auth when all three conditions are met:
    1. LOCALHOST_AUTH_BYPASS is enabled
    2. The request comes from a loopback address
    3. The path is NOT an admin API route
    """
    is_admin_path = request.url.path.startswith(_ADMIN_API_PREFIX)
    if is_admin_path:
        return False
    if not get_settings().localhost_auth_bypass:
        return False
    return is_localhost_request(request)


async def verify_admin_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    admin_key: str | None = Depends(get_admin_key),
) -> str:
    """Verify admin authentication via session cookie or API key.

    Accepts authentication via (checked in order):
    0. Localhost bypass (if enabled and not an admin API route)
    1. Session cookie (set by /auth/login)
    2. Bearer token in Authorization header
    3. x-api-key header

    Uses constant-time comparison to prevent timing attacks.

    Args:
        request: FastAPI request object
        credentials: HTTP Bearer credentials
        admin_key: Admin API key from dependencies

    Returns:
        Authentication token/key if valid

    Raises:
        HTTPException: 500 if admin key not configured, 403 if invalid or missing
    """
    if _should_bypass_auth(request):
        return "localhost-bypass"

    if not admin_key:
        raise HTTPException(
            status_code=500,
            detail="Admin authentication not configured (ADMIN_API_KEY not set)",
        )

    # Check session cookie first (for browser access)
    session_token = get_session_user(request, admin_key)
    if session_token:
        return session_token

    # Check Bearer token in Authorization header
    if credentials and secrets.compare_digest(credentials.credentials, admin_key):
        return credentials.credentials

    # Check x-api-key header
    x_api_key = request.headers.get("x-api-key")
    if x_api_key and secrets.compare_digest(x_api_key, admin_key):
        return x_api_key

    raise HTTPException(
        status_code=403,
        detail="Admin access required. Provide valid admin API key via Authorization header.",
    )


def check_auth_or_redirect(request: Request, admin_key: str | None) -> RedirectResponse | None:
    """Check if user is authenticated, return redirect if not.

    Accepts session cookies, Bearer tokens, and x-api-key headers
    (same methods as verify_admin_token).

    Returns None if authenticated, RedirectResponse to login otherwise.
    """
    if _should_bypass_auth(request):
        return None

    if not admin_key:
        return None

    session = get_session_user(request, admin_key)
    if session:
        return None

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token and secrets.compare_digest(token, admin_key):
            return None

    x_api_key = request.headers.get("x-api-key")
    if x_api_key and secrets.compare_digest(x_api_key, admin_key):
        return None

    next_url = quote(str(request.url.path), safe="")
    return RedirectResponse(url=f"/login?error=required&next={next_url}", status_code=303)


def get_base_url(request: Request) -> str:
    """Derive the external base URL from the incoming request.

    Behind reverse proxies (Railway, Heroku, etc.), the internal request uses HTTP
    but the proxy handles HTTPS. We check X-Forwarded-Proto to use the correct scheme.
    """
    base_url = str(request.base_url).rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto == "https" and base_url.startswith("http://"):
        base_url = "https://" + base_url[7:]
    return base_url


__all__ = [
    "verify_admin_token",
    "security",
    "check_auth_or_redirect",
    "get_base_url",
    "is_localhost_request",
]
