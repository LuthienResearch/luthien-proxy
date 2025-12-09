"""Session-based authentication for browser access to admin/debug UIs.

Provides a simple login flow:
1. User visits protected page -> redirected to /login
2. User enters ADMIN_API_KEY -> POST /auth/login
3. Server validates key, sets signed session cookie
4. User redirected to original page, cookie authenticates future requests
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from luthien_proxy.dependencies import get_admin_key

if TYPE_CHECKING:
    pass

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE_NAME = "luthien_session"
SESSION_MAX_AGE = 60 * 60 * 24  # 24 hours


def _get_session_secret(admin_key: str) -> str:
    """Derive a session signing secret from the admin key.

    Uses the admin key to derive the secret so we don't need another env var.
    The derived secret is used to sign session cookies.
    """
    return hashlib.sha256(f"luthien-session-{admin_key}".encode()).hexdigest()


def _create_session_token(admin_key: str) -> str:
    """Create a signed session token.

    Token format: {timestamp}.{random_id}.{signature}
    """
    timestamp = str(int(time.time()))
    random_id = secrets.token_urlsafe(16)
    payload = f"{timestamp}.{random_id}"

    secret = _get_session_secret(admin_key)
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    return f"{payload}.{signature}"


def _verify_session_token(token: str, admin_key: str) -> bool:
    """Verify a session token is valid and not expired.

    Returns True if:
    - Token format is valid
    - Signature matches
    - Token is not expired (within SESSION_MAX_AGE)
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False

        timestamp_str, random_id, signature = parts
        payload = f"{timestamp_str}.{random_id}"

        # Verify signature
        secret = _get_session_secret(admin_key)
        expected_signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            return False

        # Check expiration
        timestamp = int(timestamp_str)
        if time.time() - timestamp > SESSION_MAX_AGE:
            return False

        return True
    except (ValueError, TypeError):
        return False


def get_session_user(request: Request, admin_key: str | None) -> str | None:
    """Extract and validate session from cookie.

    Returns the session token if valid, None otherwise.
    """
    if not admin_key:
        return None

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    if _verify_session_token(token, admin_key):
        return token
    return None


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    password: str = Form(...),
    admin_key: str | None = Depends(get_admin_key),
    next_url: str = Form(default="/"),
) -> RedirectResponse:
    """Handle login form submission.

    Validates the password against ADMIN_API_KEY and sets a session cookie.
    """
    if not admin_key:
        raise HTTPException(
            status_code=500,
            detail="Admin authentication not configured (ADMIN_API_KEY not set)",
        )

    if not secrets.compare_digest(password, admin_key):
        # Redirect back to login with error
        return RedirectResponse(
            url=f"/login?error=invalid&next={next_url}",
            status_code=303,
        )

    # Create session and set cookie
    token = _create_session_token(admin_key)
    redirect = RedirectResponse(url=next_url, status_code=303)
    redirect.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return redirect


@router.post("/logout")
async def logout(next_url: str = Form(default="/login")) -> RedirectResponse:
    """Handle logout - clear session cookie."""
    redirect = RedirectResponse(url=next_url, status_code=303)
    redirect.delete_cookie(key=SESSION_COOKIE_NAME)
    return redirect


@router.get("/logout")
async def logout_get() -> RedirectResponse:
    """Handle GET logout for convenience."""
    redirect = RedirectResponse(url="/login", status_code=303)
    redirect.delete_cookie(key=SESSION_COOKIE_NAME)
    return redirect


def _escape_html_attr(value: str) -> str:
    """Escape a string for use in an HTML attribute value."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def get_login_page_html(error: str | None = None, next_url: str = "/") -> str:
    """Generate the login page HTML."""
    error_html = ""
    if error == "invalid":
        error_html = '<div class="error">Invalid password. Please try again.</div>'
    elif error == "required":
        error_html = '<div class="error">Login required to access this page.</div>'

    # Escape the next_url to prevent XSS
    safe_next_url = _escape_html_attr(next_url)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Luthien Proxy</title>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #e0e0e0;
        }}
        .login-container {{
            background: #1e1e2e;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            width: 100%;
            max-width: 400px;
        }}
        h1 {{
            text-align: center;
            margin-bottom: 8px;
            color: #fff;
            font-size: 24px;
        }}
        .subtitle {{
            text-align: center;
            color: #888;
            margin-bottom: 30px;
            font-size: 14px;
        }}
        .error {{
            background: #ff6b6b22;
            border: 1px solid #ff6b6b;
            color: #ff6b6b;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 20px;
            text-align: center;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            margin-bottom: 8px;
            font-weight: 500;
            color: #ccc;
        }}
        input[type="password"] {{
            width: 100%;
            padding: 12px 16px;
            border: 1px solid #333;
            border-radius: 6px;
            background: #2a2a3e;
            color: #fff;
            font-size: 16px;
            transition: border-color 0.2s;
        }}
        input[type="password"]:focus {{
            outline: none;
            border-color: #6366f1;
        }}
        button {{
            width: 100%;
            padding: 14px;
            background: #6366f1;
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }}
        button:hover {{
            background: #5558e3;
        }}
        .back-link {{
            text-align: center;
            margin-top: 20px;
        }}
        .back-link a {{
            color: #888;
            text-decoration: none;
        }}
        .back-link a:hover {{
            color: #6366f1;
        }}
    </style>
</head>
<body>
    <div class="login-container">
        <h1>Luthien Proxy</h1>
        <p class="subtitle">Admin Authentication Required</p>
        {error_html}
        <form method="POST" action="/auth/login">
            <input type="hidden" name="next" value="{safe_next_url}">
            <div class="form-group">
                <label for="password">Admin API Key</label>
                <input type="password" id="password" name="password" required autofocus
                       placeholder="Enter your admin API key...">
            </div>
            <button type="submit">Sign In</button>
        </form>
        <div class="back-link">
            <a href="/">← Back to Home</a>
        </div>
    </div>
</body>
</html>"""


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    error: str | None = None,
    next: str = "/",
) -> HTMLResponse:
    """Serve the login page."""
    return HTMLResponse(get_login_page_html(error=error, next_url=next))


# Also serve at /login for convenience
login_page_router = APIRouter(tags=["auth"])


@login_page_router.get("/login", response_class=HTMLResponse)
async def login_page_root(
    request: Request,
    error: str | None = None,
    next: str = "/",
) -> HTMLResponse:
    """Serve the login page at /login."""
    return HTMLResponse(get_login_page_html(error=error, next_url=next))


__all__ = [
    "router",
    "login_page_router",
    "get_session_user",
    "SESSION_COOKIE_NAME",
]
