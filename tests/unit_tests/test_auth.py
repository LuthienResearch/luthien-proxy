
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

from luthien_proxy.auth import check_auth_or_redirect, verify_admin_token
from luthien_proxy.dependencies import Dependencies
from luthien_proxy.llm.client import LLMClient
from luthien_proxy.observability.emitter import NullEventEmitter
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_manager import PolicyManager


class MockLLMClient(LLMClient):
    """Mock LLM client for testing."""

    async def stream(self, request):
        """Mock stream."""
        yield MagicMock()

    async def complete(self, request):
        """Mock complete."""
        return MagicMock()


@pytest.fixture
def app_with_admin_key():
    """Create a FastAPI app with admin key configured."""
    app = FastAPI()

    mock_policy_manager = MagicMock(spec=PolicyManager)
    mock_policy_manager.current_policy = NoOpPolicy()

    deps = Dependencies(
        db_pool=None,
        redis_client=None,
        llm_client=MockLLMClient(),
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
        llm_client=MockLLMClient(),
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
