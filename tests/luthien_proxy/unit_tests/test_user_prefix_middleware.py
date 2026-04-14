"""Tests for UserPrefixMiddleware in main.py.

The middleware extracts usernames from /u/{name}/ URL prefixes,
stores them on request.state.luthien_user, and strips the prefix
from the path so downstream routes see the original URL structure.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.credential_manager import AuthMode
from luthien_proxy.main import create_app


@pytest.fixture
def app(policy_config_file, mock_db_pool, mock_redis_client):
    """Create a test app with the UserPrefixMiddleware active."""
    return create_app(
        api_key="test-key",
        admin_key=None,
        db_pool=mock_db_pool,
        redis_client=mock_redis_client,
        startup_policy_path=policy_config_file,
        auth_mode=AuthMode.PASSTHROUGH,
    )


@pytest.fixture
def policy_config_file(tmp_path):
    """Create a temporary policy config file."""
    config_path = tmp_path / "policy.yaml"
    config_path.write_text('policy:\n  class: "luthien_proxy.policies.noop_policy:NoOpPolicy"\n  config: {}\n')
    return str(config_path)


@pytest.fixture
def mock_db_pool():
    mock = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock.get_pool = AsyncMock(return_value=mock_pool)
    mock.close = AsyncMock()
    mock.is_sqlite = False
    return mock


@pytest.fixture
def mock_redis_client():
    mock = AsyncMock()
    mock.ping = AsyncMock()
    mock.close = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    return mock


class TestUserPrefixMiddleware:
    """Test UserPrefixMiddleware path rewriting and state injection."""

    def test_strips_user_prefix_and_routes_to_health(self, app):
        """Requests to /u/{name}/health should reach the /health endpoint."""
        with TestClient(app) as client:
            response = client.get("/u/stefan/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

    def test_no_op_for_paths_without_prefix(self, app):
        """Paths that don't start with /u/ should be unaffected."""
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

    def test_username_stored_on_request_state(self, app):
        """The extracted username should be accessible via request.state.luthien_user.

        We verify indirectly: the /health endpoint works through the middleware,
        and we can test by adding a custom endpoint that reads request.state.
        """
        from fastapi import Request
        from fastapi.responses import JSONResponse

        @app.get("/test-user-state")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "luthien_user", None)
            return JSONResponse({"luthien_user": user})

        with TestClient(app) as client:
            # With prefix
            response = client.get("/u/alice/test-user-state")
            assert response.status_code == 200
            assert response.json()["luthien_user"] == "alice"

            # Without prefix
            response = client.get("/test-user-state")
            assert response.status_code == 200
            assert response.json()["luthien_user"] is None

    def test_username_capped_at_64_chars(self, app):
        """Usernames longer than 64 characters should be truncated."""
        from fastapi import Request
        from fastapi.responses import JSONResponse

        @app.get("/test-user-len")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "luthien_user", None)
            return JSONResponse({"luthien_user": user})

        long_name = "a" * 100
        with TestClient(app) as client:
            response = client.get(f"/u/{long_name}/test-user-len")
            assert response.status_code == 200
            username = response.json()["luthien_user"]
            assert len(username) == 64
            assert username == "a" * 64

    def test_path_with_only_u_prefix_no_rest(self, app):
        """Paths like /u/name (no trailing slash or rest) should pass through as-is.

        The middleware requires at least 4 parts: ['', 'u', 'name', 'rest...'].
        /u/name only splits into 3 parts, so the middleware is a no-op.
        """
        with TestClient(app) as client:
            response = client.get("/u/stefan")
            # This path won't match any route after middleware is a no-op,
            # so it should 404.
            assert response.status_code == 404

    def test_nested_path_after_prefix(self, app):
        """Paths like /u/{name}/nested/path should have the prefix stripped."""
        from fastapi import Request
        from fastapi.responses import JSONResponse

        @app.get("/nested/test-path")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "luthien_user", None)
            return JSONResponse({"luthien_user": user, "path": request.scope.get("path")})

        with TestClient(app) as client:
            response = client.get("/u/bob/nested/test-path")
            assert response.status_code == 200
            data = response.json()
            assert data["luthien_user"] == "bob"
            assert data["path"] == "/nested/test-path"
