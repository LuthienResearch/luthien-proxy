# ABOUTME: Tests role separation between CLIENT_API_KEY and ADMIN_API_KEY
# ABOUTME: Covers issue #555 — proxy key rejected on admin surface, fail-closed default

"""Role-separation auth tests (issue #555; supersedes PR #574).

Covers the behaviours that close #555 properly:

- ``verify_admin_token``: the proxy key (CLIENT_API_KEY) is rejected on admin
  API endpoints with a clear 403, including the mixed case where a garbage
  Bearer accompanies a proxy ``x-api-key`` (the edge case PR #574 missed).
- ``check_auth_or_redirect``: **fails closed** when ADMIN_API_KEY is unset
  (the real, pre-existing gap PR #574 left open), and surfaces
  ``error=proxy_key`` for proxy-key attempts.
- login POST + login page: proxy-key and not-configured messaging.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from luthien_proxy.auth import check_auth_or_redirect, verify_admin_token
from luthien_proxy.dependencies import GatewayDependencies as Dependencies
from luthien_proxy.dependencies import get_admin_key, get_api_key
from luthien_proxy.observability.emitter import NullEventEmitter
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.session import get_login_page_html
from luthien_proxy.session import router as auth_router

CLIENT = "client-key"
ADMIN = "admin-key"


def _make_app(api_key: str | None, admin_key: str | None) -> FastAPI:
    """App exposing an admin API route, an admin UI route, and the auth router."""
    app = FastAPI()
    mock_pm = MagicMock(spec=PolicyManager)
    mock_pm.current_policy = NoOpPolicy()
    app.state.dependencies = Dependencies(
        db_pool=None,
        redis_client=None,
        policy_manager=mock_pm,
        emitter=NullEventEmitter(),
        api_key=api_key,
        admin_key=admin_key,
    )

    @app.get("/admin-api")
    async def admin_api(token: str = Depends(verify_admin_token)):
        return {"token": token}

    @app.get("/admin-page")
    async def admin_page(
        request: Request,
        ak: str | None = Depends(get_admin_key),
        ck: str | None = Depends(get_api_key),
    ):
        redirect = check_auth_or_redirect(request, ak, client_api_key=ck)
        if redirect:
            return redirect
        return {"ok": True}

    app.include_router(auth_router)
    return app


# --- verify_admin_token (admin API surface) ---------------------------------


def test_verify_rejects_client_key_via_bearer():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.get("/admin-api", headers={"Authorization": f"Bearer {CLIENT}"})
    assert resp.status_code == 403
    assert "CLIENT_API_KEY" in resp.json()["detail"]


def test_verify_rejects_client_key_via_x_api_key():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.get("/admin-api", headers={"x-api-key": CLIENT})
    assert resp.status_code == 403
    assert "CLIENT_API_KEY" in resp.json()["detail"]


def test_verify_role_hint_survives_garbage_bearer():
    """Edge case PR #574 missed: garbage Bearer + proxy x-api-key still hints."""
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.get(
        "/admin-api",
        headers={"Authorization": "Bearer garbage", "x-api-key": CLIENT},
    )
    assert resp.status_code == 403
    assert "CLIENT_API_KEY" in resp.json()["detail"]


def test_verify_admin_key_is_accepted():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.get("/admin-api", headers={"Authorization": f"Bearer {ADMIN}"})
    assert resp.status_code == 200
    assert resp.json()["token"] == ADMIN


def test_verify_shared_key_grants_access():
    """CLIENT_API_KEY == ADMIN_API_KEY (local dev) still authenticates."""
    client = TestClient(_make_app(ADMIN, ADMIN))
    resp = client.get("/admin-api", headers={"Authorization": f"Bearer {ADMIN}"})
    assert resp.status_code == 200


def test_verify_unknown_key_gets_generic_403():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.get("/admin-api", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 403
    assert "CLIENT_API_KEY" not in resp.json()["detail"]


# --- check_auth_or_redirect (admin UI surface) ------------------------------


def test_check_auth_fails_closed_when_admin_key_unset():
    """The real gap PR #574 left open: unset ADMIN_API_KEY must not serve the UI."""
    client = TestClient(_make_app(CLIENT, None))
    resp = client.get("/admin-page", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=not_configured" in resp.headers["location"]


def test_check_auth_required_when_unauthenticated():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.get("/admin-page", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=required" in resp.headers["location"]


def test_check_auth_proxy_key_redirect():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.get("/admin-page", headers={"x-api-key": CLIENT}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=proxy_key" in resp.headers["location"]


def test_check_auth_proxy_key_survives_garbage_bearer():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.get(
        "/admin-page",
        headers={"Authorization": "Bearer garbage", "x-api-key": CLIENT},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error=proxy_key" in resp.headers["location"]


def test_check_auth_admin_key_allows():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.get("/admin-page", headers={"x-api-key": ADMIN}, follow_redirects=False)
    assert resp.status_code == 200


# --- login POST + login page ------------------------------------------------


def test_login_post_proxy_key_is_role_aware():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.post("/auth/login", data={"password": CLIENT, "next_url": "/"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=proxy_key" in resp.headers["location"]


def test_login_post_wrong_password_is_invalid():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.post("/auth/login", data={"password": "nope", "next_url": "/"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=invalid" in resp.headers["location"]


def test_login_post_admin_key_sets_session():
    client = TestClient(_make_app(CLIENT, ADMIN))
    resp = client.post("/auth/login", data={"password": ADMIN, "next_url": "/"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "luthien_session" in resp.headers.get("set-cookie", "")


def test_login_page_renders_proxy_key_message():
    html = get_login_page_html(error="proxy_key")
    assert "CLIENT_API_KEY" in html
    assert "ADMIN_API_KEY" in html


def test_login_page_renders_not_configured_message():
    html = get_login_page_html(error="not_configured")
    assert "not configured" in html.lower()
