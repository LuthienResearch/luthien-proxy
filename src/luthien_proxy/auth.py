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

The bypass refuses any request that carries reverse-proxy forwarding
headers (X-Forwarded-For, Forwarded, X-Real-IP, etc.), even when the TCP
source IP is loopback. A reverse proxy on the same host (Caddy, nginx,
Traefik) makes every external request arrive from 127.0.0.1; the
forwarding headers those proxies attach are the signal that the true
client is remote. A client can also set these headers directly, but that
only *disables* the bypass for them (fail-safe). The residual risk is a
same-host reverse proxy configured to strip/omit all forwarding headers
— set LOCALHOST_AUTH_BYPASS=false for any reverse-proxy deployment.
Railway disables the bypass automatically at startup.
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

_LOCALHOST_IPS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")

# Headers a reverse proxy attaches when forwarding a request. Their presence
# on a loopback connection means the true client is (or may be) remote, so
# the localhost bypass must not apply. Standard proxies set at least one of
# these by default: Caddy and Traefik set X-Forwarded-For automatically;
# common nginx configs set X-Forwarded-For and/or X-Real-IP.
_FORWARDING_HEADERS = (
    "forwarded",  # RFC 7239
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
)


def is_localhost_request(request: Request) -> bool:
    """Check whether the request originates from a loopback address."""
    client = request.client
    if client is None:
        return False
    return client.host in _LOCALHOST_IPS


def has_forwarding_headers(request: Request) -> bool:
    """Check whether the request carries reverse-proxy forwarding headers."""
    return any(header in request.headers for header in _FORWARDING_HEADERS)


def _should_bypass_auth(request: Request) -> bool:
    """Return True if auth can be skipped for this request.

    Requires all three:
    1. LOCALHOST_AUTH_BYPASS enabled (default true),
    2. loopback TCP source address, and
    3. no reverse-proxy forwarding headers — a proxied request is not
       treated as local even though the proxy connects from 127.0.0.1.
    """
    if not get_settings().localhost_auth_bypass:
        return False
    if not is_localhost_request(request):
        return False
    return not has_forwarding_headers(request)


async def verify_admin_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    admin_key: str | None = Depends(get_admin_key),
) -> str:
    """Verify admin authentication via session cookie or API key.

    Accepts authentication via (checked in order):
    0. Localhost bypass (if enabled)
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
        # Fail closed: with no ADMIN_API_KEY configured, the admin UI must not be
        # served. verify_admin_token rejects in the same situation; this path
        # previously returned None (= authenticated), leaving the admin/history
        # UI open. Localhost bypass above still lets dockerless local dev through.
        next_url = quote(str(request.url.path), safe="")
        return RedirectResponse(url=f"/login?error=required&next={next_url}", status_code=303)

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
    "has_forwarding_headers",
    "is_localhost_request",
]
