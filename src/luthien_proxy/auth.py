"""Shared authentication utilities for admin and debug endpoints.

Supports three authentication methods:
1. Session cookie (for browser access after login)
2. Bearer token in Authorization header (for API access)
3. x-api-key header (for API access)

Localhost bypass: when LOCALHOST_AUTH_BYPASS=true (default), requests from
127.0.0.1 or ::1 skip auth for the routes that go through this module —
i.e. admin API, debug, history, request_log, and the UI login redirect.
The proxy route `/v1/messages` uses its own verify_token() in
gateway_routes.py, which does NOT consult this module and is therefore
unaffected by the bypass.

WARNING: is_localhost_request() inspects request.client.host (TCP source
IP) only; it does not parse X-Forwarded-For. A reverse proxy on the same
host (Caddy, nginx, Traefik) forwards every external request as
127.0.0.1 and silently unauths the admin API. Set
LOCALHOST_AUTH_BYPASS=false for any such deployment. Railway disables
the bypass automatically at startup.
"""

from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.dependencies import get_admin_key, get_api_key
from luthien_proxy.session import get_session_user
from luthien_proxy.settings import get_settings

security = HTTPBearer(auto_error=False)

_LOCALHOST_IPS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def is_localhost_request(request: Request) -> bool:
    """Check whether the request originates from a loopback address."""
    client = request.client
    if client is None:
        return False
    return client.host in _LOCALHOST_IPS


def _should_bypass_auth(request: Request) -> bool:
    """Return True if auth can be skipped for this request."""
    if not get_settings().localhost_auth_bypass:
        return False
    return is_localhost_request(request)


async def verify_admin_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    admin_key: str | None = Depends(get_admin_key),
    client_api_key: str | None = Depends(get_api_key),
) -> str:
    """Verify admin authentication via session cookie or API key.

    Accepts authentication via (checked in order):
    0. Localhost bypass (if enabled)
    1. Session cookie (set by /auth/login)
    2. Bearer token in Authorization header
    3. x-api-key header

    Uses constant-time comparison to prevent timing attacks.

    Role separation: if the presented key matches CLIENT_API_KEY but not
    ADMIN_API_KEY, the request is actively rejected with a 403 and a clear
    message. This prevents proxy keys from accidentally accessing admin
    endpoints. Exception: if CLIENT_API_KEY == ADMIN_API_KEY (local dev
    convenience), the admin key check passes first and access is granted.

    Args:
        request: FastAPI request object
        credentials: HTTP Bearer credentials
        admin_key: Admin API key from dependencies
        client_api_key: Proxy client API key from dependencies (CLIENT_API_KEY)

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

    # Collect the presented token (Bearer or x-api-key)
    presented_token = credentials.credentials if credentials else request.headers.get("x-api-key")

    # Check Bearer token in Authorization header
    if credentials and secrets.compare_digest(credentials.credentials, admin_key):
        return credentials.credentials

    # Check x-api-key header
    x_api_key = request.headers.get("x-api-key")
    if x_api_key and secrets.compare_digest(x_api_key, admin_key):
        return x_api_key

    # Active rejection: if the key matches CLIENT_API_KEY, give a clear error
    # rather than a generic "wrong key" message. This enforces role separation.
    if presented_token and client_api_key and secrets.compare_digest(presented_token, client_api_key):
        raise HTTPException(
            status_code=403,
            detail=(
                "Proxy API key (CLIENT_API_KEY) cannot be used for admin access. Use ADMIN_API_KEY for admin endpoints."
            ),
        )

    raise HTTPException(
        status_code=403,
        detail="Admin access required. Provide valid admin API key via Authorization header.",
    )


def check_auth_or_redirect(
    request: Request,
    admin_key: str | None,
    client_api_key: str | None = None,
) -> RedirectResponse | None:
    """Check if user is authenticated, return redirect if not.

    Accepts session cookies, Bearer tokens, and x-api-key headers
    (same methods as verify_admin_token).

    Role separation: if the presented key matches CLIENT_API_KEY but not
    ADMIN_API_KEY, the request is redirected to login (not granted access).
    Exception: if CLIENT_API_KEY == ADMIN_API_KEY, the admin key check
    passes first and access is granted.

    Args:
        request: FastAPI request object
        admin_key: Admin API key (ADMIN_API_KEY)
        client_api_key: Proxy client API key (CLIENT_API_KEY), optional

    Returns None if authenticated, RedirectResponse to login otherwise.
    """
    if _should_bypass_auth(request):
        return None

    if not admin_key:
        return RedirectResponse(url="/login?error=not_configured", status_code=303)

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

    # If the presented key matches CLIENT_API_KEY (but not ADMIN_API_KEY, since
    # we already checked above), redirect with a specific error code so the login
    # page can show a helpful message about role separation.
    presented_token: str | None = None
    if auth_header.startswith("Bearer "):
        presented_token = auth_header[7:] or None
    if presented_token is None:
        presented_token = request.headers.get("x-api-key") or None

    if presented_token and client_api_key and secrets.compare_digest(presented_token, client_api_key):
        next_url = quote(str(request.url.path), safe="")
        return RedirectResponse(url=f"/login?error=proxy_key&next={next_url}", status_code=303)

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
