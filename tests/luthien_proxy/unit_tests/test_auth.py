# ABOUTME: Unit tests for shared authentication module
# ABOUTME: Tests verify_admin_token and check_auth_or_redirect functions

"""Tests for auth module.

Tests the verify_admin_token function which handles authentication for
admin and debug endpoints, and check_auth_or_redirect which gates
HTML-serving endpoints (live view, activity monitor, etc.).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from luthien_proxy.auth import (
    check_auth_or_redirect,
    is_localhost_request,
    verify_admin_token,
)
from luthien_proxy.dependencies import Dependencies
from luthien_proxy.observability.emitter import NullEventEmitter
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.settings import clear_settings_cache


@pytest.fixture
def app_with_admin_key():
    """Create a FastAPI app with admin key configured."""
    app = FastAPI()

    mock_policy_manager = MagicMock(spec=PolicyManager)
    mock_policy_manager.current_policy = NoOpPolicy()

    deps = Dependencies(
        db_pool=None,
        redis_client=None,
        policy_manager=mock_policy_manager,
        emitter=NullEventEmitter(),
        api_key="test-api-key",
        admin_key="test-admin-key",
    )

    app.state.dependencies = deps

    @app.get("/test")
    async def test_endpoint(token: str = Depends(verify_admin_token)):
        return {"authenticated": True, "token": token}

    return app


@pytest.fixture
def app_without_admin_key():
    """Create a FastAPI app without admin key configured."""
    app = FastAPI()

    mock_policy_manager = MagicMock(spec=PolicyManager)
    mock_policy_manager.current_policy = NoOpPolicy()

    deps = Dependencies(
        db_pool=None,
        redis_client=None,
        policy_manager=mock_policy_manager,
        emitter=NullEventEmitter(),
        api_key="test-api-key",
        admin_key=None,
    )

    app.state.dependencies = deps

    @app.get("/test")
    async def test_endpoint(token: str = Depends(verify_admin_token)):
        return {"authenticated": True}

    return app


class TestVerifyAdminTokenBearerAuth:
    """Test Bearer token authentication."""

    def test_valid_bearer_token(self, app_with_admin_key):
        """Test authentication with valid Bearer token."""
        with TestClient(app_with_admin_key) as client:
            response = client.get(
                "/test",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert response.status_code == 200
            assert response.json()["authenticated"] is True
            assert response.json()["token"] == "test-admin-key"

    def test_invalid_bearer_token(self, app_with_admin_key):
        """Test authentication with invalid Bearer token."""
        with TestClient(app_with_admin_key) as client:
            response = client.get(
                "/test",
                headers={"Authorization": "Bearer wrong-key"},
            )
            assert response.status_code == 403
            assert "Admin access required" in response.json()["detail"]

    def test_missing_bearer_token(self, app_with_admin_key):
        """Test authentication without any auth header."""
        with TestClient(app_with_admin_key) as client:
            response = client.get("/test")
            assert response.status_code == 403
            assert "Admin access required" in response.json()["detail"]


class TestVerifyAdminTokenXApiKeyAuth:
    """Test x-api-key header authentication."""

    def test_valid_x_api_key(self, app_with_admin_key):
        """Test authentication with valid x-api-key header."""
        with TestClient(app_with_admin_key) as client:
            response = client.get(
                "/test",
                headers={"x-api-key": "test-admin-key"},
            )
            assert response.status_code == 200
            assert response.json()["authenticated"] is True
            assert response.json()["token"] == "test-admin-key"

    def test_invalid_x_api_key(self, app_with_admin_key):
        """Test authentication with invalid x-api-key header."""
        with TestClient(app_with_admin_key) as client:
            response = client.get(
                "/test",
                headers={"x-api-key": "wrong-key"},
            )
            assert response.status_code == 403
            assert "Admin access required" in response.json()["detail"]


class TestVerifyAdminTokenMissingConfig:
    """Test behavior when admin key is not configured."""

    def test_returns_500_when_admin_key_not_configured(self, app_without_admin_key):
        """Test that 500 is returned when ADMIN_API_KEY is not set."""
        with TestClient(app_without_admin_key) as client:
            response = client.get(
                "/test",
                headers={"Authorization": "Bearer some-key"},
            )
            assert response.status_code == 500
            assert "not configured" in response.json()["detail"]


class TestVerifyAdminTokenEdgeCases:
    """Test edge cases for authentication."""

    def test_bearer_takes_priority_over_x_api_key(self, app_with_admin_key):
        """Test that valid Bearer token is used even if x-api-key is also present."""
        with TestClient(app_with_admin_key) as client:
            response = client.get(
                "/test",
                headers={
                    "Authorization": "Bearer test-admin-key",
                    "x-api-key": "wrong-key",
                },
            )
            assert response.status_code == 200
            assert response.json()["token"] == "test-admin-key"

    def test_x_api_key_used_when_bearer_invalid(self, app_with_admin_key):
        """Test that x-api-key is checked when Bearer token is invalid."""
        with TestClient(app_with_admin_key) as client:
            response = client.get(
                "/test",
                headers={
                    "Authorization": "Bearer wrong-key",
                    "x-api-key": "test-admin-key",
                },
            )
            assert response.status_code == 200
            assert response.json()["token"] == "test-admin-key"

    def test_empty_bearer_token_rejected(self, app_with_admin_key):
        """Test that empty Bearer token is rejected."""
        with TestClient(app_with_admin_key) as client:
            response = client.get(
                "/test",
                headers={"Authorization": "Bearer "},
            )
            assert response.status_code == 403

    def test_empty_x_api_key_rejected(self, app_with_admin_key):
        """Test that empty x-api-key is rejected."""
        with TestClient(app_with_admin_key) as client:
            response = client.get(
                "/test",
                headers={"x-api-key": ""},
            )
            assert response.status_code == 403


def _make_request(headers: dict[str, str] | None = None, path: str = "/live") -> MagicMock:
    """Build a minimal mock Request for check_auth_or_redirect tests."""
    request = MagicMock()
    request.headers = headers or {}
    request.cookies = {}
    request.url.path = path
    return request


class TestCheckAuthOrRedirectNoKey:
    """When admin_key is None, everything passes through."""

    def test_returns_none_when_no_admin_key(self):
        result = check_auth_or_redirect(_make_request(), admin_key=None)
        assert result is None


class TestCheckAuthOrRedirectBearer:
    """Bearer token authentication in check_auth_or_redirect."""

    def test_valid_bearer_returns_none(self):
        request = _make_request(headers={"authorization": "Bearer secret123"})
        assert check_auth_or_redirect(request, admin_key="secret123") is None

    def test_invalid_bearer_redirects(self):
        request = _make_request(headers={"authorization": "Bearer wrong"})
        result = check_auth_or_redirect(request, admin_key="secret123")
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303

    def test_empty_bearer_redirects(self):
        request = _make_request(headers={"authorization": "Bearer "})
        result = check_auth_or_redirect(request, admin_key="secret123")
        assert isinstance(result, RedirectResponse)


class TestCheckAuthOrRedirectXApiKey:
    """x-api-key header authentication in check_auth_or_redirect."""

    def test_valid_x_api_key_returns_none(self):
        request = _make_request(headers={"x-api-key": "secret123"})
        assert check_auth_or_redirect(request, admin_key="secret123") is None

    def test_invalid_x_api_key_redirects(self):
        request = _make_request(headers={"x-api-key": "wrong"})
        result = check_auth_or_redirect(request, admin_key="secret123")
        assert isinstance(result, RedirectResponse)

    def test_empty_x_api_key_redirects(self):
        request = _make_request(headers={"x-api-key": ""})
        result = check_auth_or_redirect(request, admin_key="secret123")
        assert isinstance(result, RedirectResponse)


class TestCheckAuthOrRedirectFallthrough:
    """No auth at all results in redirect."""

    def test_no_headers_redirects(self):
        result = check_auth_or_redirect(_make_request(), admin_key="secret123")
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303

    def test_redirect_includes_next_url(self):
        result = check_auth_or_redirect(
            _make_request(path="/live/conv/123"),
            admin_key="secret123",
        )
        assert isinstance(result, RedirectResponse)
        location = dict(result.headers)["location"]
        assert "/login" in location
        assert "next=" in location


# --- Localhost bypass tests ---


def _make_localhost_request(
    host: str = "127.0.0.1",
    path: str = "/activity/monitor",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a mock Request that appears to come from a given IP."""
    request = MagicMock()
    request.headers = headers or {}
    request.cookies = {}
    request.url.path = path
    request.client.host = host
    return request


class TestIsLocalhostRequest:
    """Test the is_localhost_request helper."""

    def test_ipv4_loopback(self):
        request = _make_localhost_request(host="127.0.0.1")
        assert is_localhost_request(request) is True

    def test_ipv6_loopback(self):
        request = _make_localhost_request(host="::1")
        assert is_localhost_request(request) is True

    def test_ipv4_mapped_ipv6(self):
        request = _make_localhost_request(host="::ffff:127.0.0.1")
        assert is_localhost_request(request) is True

    def test_remote_ip(self):
        request = _make_localhost_request(host="192.168.1.50")
        assert is_localhost_request(request) is False

    def test_no_client(self):
        request = MagicMock()
        request.client = None
        assert is_localhost_request(request) is False


class TestLocalhostBypassCheckAuthOrRedirect:
    """Localhost bypass for the redirect-based auth used by UI pages."""

    @pytest.fixture(autouse=True)
    def _clear_settings(self):
        clear_settings_cache()
        yield
        clear_settings_cache()

    def test_localhost_bypasses_auth(self, monkeypatch):
        monkeypatch.setenv("LOCALHOST_AUTH_BYPASS", "true")
        request = _make_localhost_request(path="/activity/monitor")
        result = check_auth_or_redirect(request, admin_key="secret123")
        assert result is None

    def test_remote_ip_still_requires_auth(self, monkeypatch):
        monkeypatch.setenv("LOCALHOST_AUTH_BYPASS", "true")
        request = _make_localhost_request(host="10.0.0.5", path="/activity/monitor")
        result = check_auth_or_redirect(request, admin_key="secret123")
        assert isinstance(result, RedirectResponse)

    def test_bypass_disabled_requires_auth(self, monkeypatch):
        monkeypatch.setenv("LOCALHOST_AUTH_BYPASS", "false")
        request = _make_localhost_request(path="/activity/monitor")
        result = check_auth_or_redirect(request, admin_key="secret123")
        assert isinstance(result, RedirectResponse)

    def test_admin_path_bypassed_from_localhost(self, monkeypatch):
        monkeypatch.setenv("LOCALHOST_AUTH_BYPASS", "true")
        request = _make_localhost_request(path="/api/admin/policy")
        result = check_auth_or_redirect(request, admin_key="secret123")
        assert result is None


class TestLocalhostBypassVerifyAdminToken:
    """Localhost bypass for the dependency-injected auth used by API endpoints."""

    @pytest.fixture(autouse=True)
    def _clear_settings(self):
        clear_settings_cache()
        yield
        clear_settings_cache()

    @pytest.fixture
    def app_localhost_bypass(self, monkeypatch):
        """App with admin key and localhost bypass enabled."""
        monkeypatch.setenv("LOCALHOST_AUTH_BYPASS", "true")
        clear_settings_cache()

        app = FastAPI()
        mock_policy_manager = MagicMock(spec=PolicyManager)
        mock_policy_manager.current_policy = NoOpPolicy()

        deps = Dependencies(
            db_pool=None,
            redis_client=None,
            policy_manager=mock_policy_manager,
            emitter=NullEventEmitter(),
            api_key="test-api-key",
            admin_key="test-admin-key",
        )
        app.state.dependencies = deps

        @app.get("/ui-endpoint")
        async def ui_endpoint(token: str = Depends(verify_admin_token)):
            return {"token": token}

        @app.get("/api/admin/policy")
        async def admin_endpoint(token: str = Depends(verify_admin_token)):
            return {"token": token}

        return app

    def test_localhost_bypasses_ui_endpoint(self, app_localhost_bypass, monkeypatch):
        monkeypatch.setattr("luthien_proxy.auth.is_localhost_request", lambda r: True)
        with TestClient(app_localhost_bypass) as client:
            response = client.get("/ui-endpoint")
            assert response.status_code == 200
            assert response.json()["token"] == "localhost-bypass"

    def test_localhost_bypasses_admin_endpoint(self, app_localhost_bypass, monkeypatch):
        monkeypatch.setattr("luthien_proxy.auth.is_localhost_request", lambda r: True)
        with TestClient(app_localhost_bypass) as client:
            response = client.get("/api/admin/policy")
            assert response.status_code == 200
            assert response.json()["token"] == "localhost-bypass"


def _make_app_with_keys(api_key: str | None, admin_key: str | None) -> FastAPI:
    """Helper: create a FastAPI app with both api_key and admin_key configured."""
    app = FastAPI()
    mock_policy_manager = MagicMock(spec=PolicyManager)
    mock_policy_manager.current_policy = NoOpPolicy()
    deps = Dependencies(
        db_pool=None,
        redis_client=None,
        policy_manager=mock_policy_manager,
        emitter=NullEventEmitter(),
        api_key=api_key,
        admin_key=admin_key,
    )
    app.state.dependencies = deps

    @app.get("/admin")
    async def admin_endpoint(token: str = Depends(verify_admin_token)):
        return {"authenticated": True, "token": token}

    return app


class TestVerifyAdminTokenRejectsClientApiKey:
    """CLIENT_API_KEY must not grant access to admin endpoints.

    When a request presents the CLIENT_API_KEY (api_key) on an admin endpoint,
    it should receive a 403 with a clear message — not a generic auth failure.
    This enforces role separation: proxy key ≠ admin key.
    """

    def test_client_api_key_bearer_rejected_with_403(self):
        """CLIENT_API_KEY presented as Bearer token → 403 on admin endpoint."""
        app = _make_app_with_keys(api_key="proxy-key", admin_key="admin-key")
        with TestClient(app) as client:
            response = client.get("/admin", headers={"Authorization": "Bearer proxy-key"})
        assert response.status_code == 403
        assert "proxy" in response.json()["detail"].lower() or "admin" in response.json()["detail"].lower()

    def test_client_api_key_x_api_key_rejected_with_403(self):
        """CLIENT_API_KEY presented as x-api-key header → 403 on admin endpoint."""
        app = _make_app_with_keys(api_key="proxy-key", admin_key="admin-key")
        with TestClient(app) as client:
            response = client.get("/admin", headers={"x-api-key": "proxy-key"})
        assert response.status_code == 403
        assert "proxy" in response.json()["detail"].lower() or "admin" in response.json()["detail"].lower()

    def test_admin_key_still_accepted(self):
        """ADMIN_API_KEY still grants access — regression guard."""
        app = _make_app_with_keys(api_key="proxy-key", admin_key="admin-key")
        with TestClient(app) as client:
            response = client.get("/admin", headers={"Authorization": "Bearer admin-key"})
        assert response.status_code == 200
        assert response.json()["authenticated"] is True

    def test_same_key_for_both_is_accepted(self):
        """When CLIENT_API_KEY == ADMIN_API_KEY (local dev), access is granted.

        The admin key check passes first, so the key is treated as an admin key.
        This is intentional: local dev convenience where one key serves both roles.
        """
        app = _make_app_with_keys(api_key="shared-key", admin_key="shared-key")
        with TestClient(app) as client:
            response = client.get("/admin", headers={"Authorization": "Bearer shared-key"})
        assert response.status_code == 200

    def test_no_client_api_key_configured_falls_through_to_normal_auth(self):
        """When CLIENT_API_KEY is not set, behavior is unchanged (no false rejections)."""
        app = _make_app_with_keys(api_key=None, admin_key="admin-key")
        with TestClient(app) as client:
            # Wrong key → generic 403 (not a proxy-key rejection)
            response = client.get("/admin", headers={"Authorization": "Bearer some-random-key"})
        assert response.status_code == 403

    def test_no_client_api_key_configured_admin_key_accepted(self):
        """When CLIENT_API_KEY is not set, ADMIN_API_KEY still works."""
        app = _make_app_with_keys(api_key=None, admin_key="admin-key")
        with TestClient(app) as client:
            response = client.get("/admin", headers={"Authorization": "Bearer admin-key"})
        assert response.status_code == 200


class TestCheckAuthOrRedirectRejectsClientApiKey:
    """CLIENT_API_KEY must not pass check_auth_or_redirect on admin/UI endpoints."""

    def test_client_api_key_bearer_redirects(self):
        """CLIENT_API_KEY as Bearer → redirect (not None) on UI endpoint."""
        request = _make_request(headers={"authorization": "Bearer proxy-key"})
        result = check_auth_or_redirect(request, admin_key="admin-key", client_api_key="proxy-key")
        assert isinstance(result, RedirectResponse)

    def test_client_api_key_x_api_key_redirects(self):
        """CLIENT_API_KEY as x-api-key → redirect on UI endpoint."""
        request = _make_request(headers={"x-api-key": "proxy-key"})
        result = check_auth_or_redirect(request, admin_key="admin-key", client_api_key="proxy-key")
        assert isinstance(result, RedirectResponse)

    def test_admin_key_still_passes(self):
        """ADMIN_API_KEY still returns None (authenticated) — regression guard."""
        request = _make_request(headers={"authorization": "Bearer admin-key"})
        result = check_auth_or_redirect(request, admin_key="admin-key", client_api_key="proxy-key")
        assert result is None

    def test_same_key_for_both_passes(self):
        """When CLIENT_API_KEY == ADMIN_API_KEY, access is granted (local dev)."""
        request = _make_request(headers={"authorization": "Bearer shared-key"})
        result = check_auth_or_redirect(request, admin_key="shared-key", client_api_key="shared-key")
        assert result is None

    def test_no_client_api_key_configured_unchanged(self):
        """When client_api_key=None, behavior is unchanged."""
        request = _make_request(headers={"authorization": "Bearer admin-key"})
        result = check_auth_or_redirect(request, admin_key="admin-key", client_api_key=None)
        assert result is None
