# ABOUTME: Unit tests for AuthMode configuration and enforcement
# ABOUTME: Tests load_config_from_env auth_mode defaults, env var parsing,
# ABOUTME: and proxy_key / both mode enforcement at the /v1/messages endpoint.

"""Tests for AuthMode configuration and gateway enforcement."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.credential_manager import AuthMode
from luthien_proxy.main import create_app, load_config_from_env

# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_main.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def policy_config_file():
    """Temporary NoOpPolicy YAML config for tests that need a running app."""
    config_content = """
policy:
  class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
  config: {}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        config_path = f.name

    yield config_path

    Path(config_path).unlink(missing_ok=True)


@pytest.fixture
def mock_db_pool():
    """Mock database pool that returns no rows from auth_config."""
    mock = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock.get_pool = AsyncMock(return_value=mock_pool)
    mock.close = AsyncMock()
    mock.is_sqlite = False
    return mock


@pytest.fixture
def mock_redis_client():
    """Mock Redis client."""
    mock = AsyncMock()
    mock.ping = AsyncMock()
    mock.close = AsyncMock()
    return mock


# Minimal valid Anthropic request body
_MINIMAL_BODY = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 10,
}


# ---------------------------------------------------------------------------
# TestAuthModeConfig
# ---------------------------------------------------------------------------


class TestAuthModeConfig:
    """Tests that auth_mode is parsed correctly from environment variables."""

    def test_auth_mode_default_is_both(self, monkeypatch):
        """AUTH_MODE defaults to 'both' when not set."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.delenv("AUTH_MODE", raising=False)

        config = load_config_from_env(settings=Settings(_env_file=None))  # type: ignore[call-arg]

        assert config["auth_mode"] == AuthMode.BOTH

    def test_auth_mode_env_var_proxy_key(self, monkeypatch):
        """AUTH_MODE=proxy_key is parsed to AuthMode.PROXY_KEY."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("AUTH_MODE", "proxy_key")

        config = load_config_from_env(settings=Settings(_env_file=None))  # type: ignore[call-arg]

        assert config["auth_mode"] == AuthMode.PROXY_KEY

    def test_auth_mode_env_var_passthrough(self, monkeypatch):
        """AUTH_MODE=passthrough is parsed to AuthMode.PASSTHROUGH."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("AUTH_MODE", "passthrough")

        config = load_config_from_env(settings=Settings(_env_file=None))  # type: ignore[call-arg]

        assert config["auth_mode"] == AuthMode.PASSTHROUGH

    def test_auth_mode_flows_into_create_app(self, policy_config_file, mock_db_pool, mock_redis_client):
        """create_app with auth_mode=PROXY_KEY initialises a CredentialManager."""
        app = create_app(
            api_key="sk-test-proxy-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.PROXY_KEY,
        )

        with TestClient(app):
            assert app.state.dependencies.credential_manager is not None


# ---------------------------------------------------------------------------
# TestAuthModeProxyKey
# ---------------------------------------------------------------------------


class TestAuthModeProxyKey:
    """Verify that proxy_key mode enforces PROXY_API_KEY-only access."""

    @pytest.fixture
    def proxy_key_app(self, policy_config_file, mock_db_pool, mock_redis_client):
        """App configured in proxy_key auth mode."""
        return create_app(
            api_key="sk-test-proxy-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.PROXY_KEY,
        )

    def test_proxy_key_mode_accepts_proxy_key(self, proxy_key_app):
        """Correct proxy key is accepted (auth does not return 401/403)."""
        with TestClient(proxy_key_app) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-test-proxy-key"},
            )

        assert response.status_code not in (401, 403)

    def test_proxy_key_mode_rejects_unknown_key(self, proxy_key_app):
        """A key that is not the proxy key is rejected with 401."""
        with TestClient(proxy_key_app) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-anthropic-real-key"},
            )

        assert response.status_code in (401, 403)

    def test_proxy_key_mode_rejects_missing_auth(self, proxy_key_app):
        """Requests without an Authorization header are rejected with 401."""
        with TestClient(proxy_key_app) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
            )

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# TestAuthModeBoth
# ---------------------------------------------------------------------------


class TestAuthModeBoth:
    """Verify that 'both' mode accepts the proxy key and rejects missing auth."""

    @pytest.fixture
    def both_mode_app(self, policy_config_file, mock_db_pool, mock_redis_client):
        """App configured in 'both' auth mode."""
        return create_app(
            api_key="sk-test-proxy-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.BOTH,
        )

    def test_both_mode_accepts_proxy_key(self, both_mode_app):
        """The proxy key is accepted in 'both' mode (auth does not return 401/403)."""
        with TestClient(both_mode_app) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-test-proxy-key"},
            )

        assert response.status_code not in (401, 403)

    def test_both_mode_rejects_missing_auth(self, both_mode_app):
        """Requests without any credentials are rejected with 401 in 'both' mode."""
        with TestClient(both_mode_app) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
            )

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# TestAuthWithNoProxyKey — api_key=None scenarios
# ---------------------------------------------------------------------------


class TestAuthWithNoProxyKey:
    """Verify auth behavior when PROXY_API_KEY is not configured (api_key=None)."""

    @pytest.fixture
    def proxy_key_mode_no_key(self, policy_config_file, mock_db_pool, mock_redis_client):
        """App in proxy_key mode with no PROXY_API_KEY — should reject everything."""
        app = create_app(
            api_key=None,
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.PROXY_KEY,
        )
        return app

    @pytest.fixture
    def both_mode_no_key(self, policy_config_file, mock_db_pool, mock_redis_client):
        """App in both mode with no PROXY_API_KEY — falls through to passthrough."""
        app = create_app(
            api_key=None,
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.BOTH,
        )
        return app

    @pytest.fixture
    def passthrough_mode_no_key(self, policy_config_file, mock_db_pool, mock_redis_client):
        """App in passthrough mode with no PROXY_API_KEY."""
        app = create_app(
            api_key=None,
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.PASSTHROUGH,
        )
        return app

    def test_proxy_key_mode_rejects_all_when_no_key(self, proxy_key_mode_no_key):
        """proxy_key mode with no PROXY_API_KEY rejects all requests (fail-closed)."""
        with TestClient(proxy_key_mode_no_key) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-ant-some-key"},
            )
        assert response.status_code in (401, 403)

    def test_both_mode_falls_through_to_passthrough_when_no_key(self, both_mode_no_key):
        """both mode with no PROXY_API_KEY skips proxy-key check, uses passthrough.

        The request may fail downstream (500) due to mock backend, but auth should pass.
        """
        with TestClient(both_mode_no_key, raise_server_exceptions=False) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-ant-some-key"},
            )
        assert response.status_code not in (401, 403)

    def test_passthrough_mode_works_without_key(self, passthrough_mode_no_key):
        """passthrough mode works normally without PROXY_API_KEY.

        The request may fail downstream (500) due to mock backend, but auth should pass.
        """
        with TestClient(passthrough_mode_no_key, raise_server_exceptions=False) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-ant-some-key"},
            )
        assert response.status_code not in (401, 403)

    def test_missing_auth_rejected_when_no_key(self, both_mode_no_key):
        """Requests with no credentials are still rejected even without PROXY_API_KEY."""
        with TestClient(both_mode_no_key) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
            )
        assert response.status_code in (401, 403)
