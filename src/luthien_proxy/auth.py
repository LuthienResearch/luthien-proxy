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

Role separation (issue #555): ADMIN_API_KEY grants admin/history access;
CLIENT_API_KEY grants only proxy access. When the presented credential
matches CLIENT_API_KEY but not ADMIN_API_KEY the request is denied with a
clear message pointing the operator at the right key. When ADMIN_API_KEY
is unset the admin surface fails *closed* (no access), matching
verify_admin_token, so an unconfigured deployment never serves the admin
UI unauthenticated.
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


def _presented_credentials(request: Request, credentials: HTTPAuthorizationCredentials | None) -> list[str]:
    """Collect every credential the caller presented (Bearer and x-api-key).

    Both are returned independently so role-separation checks don't miss the
    case where one header holds a valid-looking-but-wrong token (e.g. a garbage
    Bearer) while the other holds the proxy key.
    """
    tokens: list[str] = []
    if credentials and credentials.credentials:
        tokens.append(credentials.credentials)
    x_api_key = request.headers.get("x-api-key")
    if x_api_key:
        tokens.append(x_api_key)
    return tokens


def _matches(tokens: list[str], key: str) -> bool:
    """Constant-time membership test of ``key`` against presented ``tokens``."""
    return any(secrets.compare_digest(token, key) for token in tokens)


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

    Role separation: if the presented credential matches CLIENT_API_KEY but not
    ADMIN_API_KEY, the request is rejected with a 403 and a clear message rather
    than a generic auth failure. Exception: if CLIENT_API_KEY == ADMIN_API_KEY
    (local dev convenience), the admin key check passes first and access is
    granted.

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

    # Check Bearer token in Authorization header
    if credentials and secrets.compare_digest(credentials.credentials, admin_key):
        return credentials.credentials

    # Check x-api-key header
    x_api_key = request.headers.get("x-api-key")
    if x_api_key and secrets.compare_digest(x_api_key, admin_key):
        return x_api_key

    # Role separation: if either presented credential is the proxy key, say so
    # explicitly instead of returning a generic "wrong key" message. Checked
    # only after the admin-key paths above, so the shared-key case is unaffected.
    if client_api_key and _matches(_presented_credentials(request, credentials), client_api_key):
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

    Fails closed: when ADMIN_API_KEY is unset the admin UI is not served
    (redirect to login with ``error=not_configured``) rather than granted —
    this matches verify_admin_token, which 500s in the same situation.

    Role separation: if the presented credential matches CLIENT_API_KEY but not
    ADMIN_API_KEY, redirect to login with ``error=proxy_key`` so the page can
    explain the operator used the wrong key.

    Returns None if authenticated, RedirectResponse to login otherwise.
    """
    if _should_bypass_auth(request):
        return None

    next_url = quote(str(request.url.path), safe="")

    if not admin_key:
        # Fail closed. The UI path previously returned None here (allow), which
        # left the admin surface open on any deployment that never set
        # ADMIN_API_KEY (e.g. the commented-out default in .env.example).
        return RedirectResponse(url=f"/login?error=not_configured&next={next_url}", status_code=303)

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

    # Role separation: surface a specific error when the proxy key was used, so
    # the login page can point the operator at ADMIN_API_KEY. Inspect Bearer and
    # x-api-key independently so a garbage Bearer doesn't mask a proxy x-api-key.
    presented: list[str] = []
    if auth_header.startswith("Bearer "):
        bearer = auth_header[7:]
        if bearer:
            presented.append(bearer)
    if x_api_key:
        presented.append(x_api_key)
    if client_api_key and _matches(presented, client_api_key):
        return RedirectResponse(url=f"/login?error=proxy_key&next={next_url}", status_code=303)

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
