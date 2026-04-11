# ABOUTME: Unit tests for AuthMode configuration and enforcement
# ABOUTME: Tests load_config_from_env auth_mode defaults, env var parsing,
# ABOUTME: and client_key / both mode enforcement at the /v1/messages endpoint.

"""Tests for AuthMode configuration and gateway enforcement."""

import logging
import os
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

        monkeypatch.setenv("CLIENT_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.delenv("AUTH_MODE", raising=False)

        config = load_config_from_env(settings=Settings(_env_file=None))  # type: ignore[call-arg]

        assert config["auth_mode"] == AuthMode.BOTH

    def test_auth_mode_env_var_client_key(self, monkeypatch):
        """AUTH_MODE=client_key is parsed to AuthMode.CLIENT_KEY."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("CLIENT_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("AUTH_MODE", "client_key")

        config = load_config_from_env(settings=Settings(_env_file=None))  # type: ignore[call-arg]

        assert config["auth_mode"] == AuthMode.CLIENT_KEY

    def test_auth_mode_env_var_passthrough(self, monkeypatch):
        """AUTH_MODE=passthrough is parsed to AuthMode.PASSTHROUGH."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("CLIENT_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("AUTH_MODE", "passthrough")

        config = load_config_from_env(settings=Settings(_env_file=None))  # type: ignore[call-arg]

        assert config["auth_mode"] == AuthMode.PASSTHROUGH

    def test_auth_mode_env_var_legacy_proxy_key_is_tolerated(self, monkeypatch, caplog):
        """Legacy AUTH_MODE=proxy_key (pre-#524) is coerced to CLIENT_KEY with a warning.

        Mirrors the DB-row tolerance: an operator who upgrades the gateway
        image but misses one env var should get a running gateway and a clear
        warning, not a cryptic Pydantic ValidationError. The legacy alias is
        removed in a follow-up release.

        Note: this test does NOT pass a pre-constructed Settings because the
        env-var coercion has to run before Settings is validated. Letting
        `load_config_from_env` build its own Settings exercises the real
        startup path.
        """
        monkeypatch.setenv("CLIENT_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("AUTH_MODE", "proxy_key")

        # Force get_settings() to rebuild so it picks up the monkeypatched env.
        from luthien_proxy.settings import clear_settings_cache

        clear_settings_cache()

        with caplog.at_level("WARNING", logger="luthien_proxy.credential_manager"):
            config = load_config_from_env()

        assert config["auth_mode"] == AuthMode.CLIENT_KEY
        assert os.environ["AUTH_MODE"] == "client_key"
        warnings = [
            r for r in caplog.records if r.name == "luthien_proxy.credential_manager" and r.levelno == logging.WARNING
        ]
        assert warnings, "expected a warning when AUTH_MODE=proxy_key is read"
        assert any("AUTH_MODE env var" in r.getMessage() for r in warnings)

    def test_auth_mode_dotenv_legacy_proxy_key_is_tolerated(self, monkeypatch, tmp_path, caplog):
        """Legacy AUTH_MODE=proxy_key in a .env file is also tolerated.

        Closes the gap flagged in review: pydantic-settings reads `.env`
        inside Settings.__init__, so a pre-coercion check on `os.environ`
        alone would miss values that only live in `.env`. `load_config_from_env`
        now pre-reads the file directly.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "CLIENT_API_KEY=test-proxy-key\n"
            "ADMIN_API_KEY=test-admin-key\n"
            "DATABASE_URL=postgresql://test:test@localhost/test\n"
            "AUTH_MODE=proxy_key\n"
        )
        monkeypatch.delenv("AUTH_MODE", raising=False)

        from luthien_proxy.settings import clear_settings_cache

        clear_settings_cache()

        with caplog.at_level("WARNING", logger="luthien_proxy.credential_manager"):
            config = load_config_from_env()

        assert config["auth_mode"] == AuthMode.CLIENT_KEY
        warnings = [
            r for r in caplog.records if r.name == "luthien_proxy.credential_manager" and r.levelno == logging.WARNING
        ]
        assert warnings, "expected a warning when AUTH_MODE=proxy_key is read from .env"

    def test_leftover_proxy_api_key_env_var_warns(self, monkeypatch, caplog):
        """A leftover PROXY_API_KEY in the environment triggers a rename warning.

        Without the warning, the old env var would be silently dropped
        (pydantic-settings `extra="ignore"`), leaving the operator confused
        about why shared-key auth stopped working.
        """
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("PROXY_API_KEY", "sk-leftover")
        monkeypatch.delenv("CLIENT_API_KEY", raising=False)
        monkeypatch.delenv("AUTH_MODE", raising=False)

        from luthien_proxy.settings import clear_settings_cache

        clear_settings_cache()

        with caplog.at_level("WARNING", logger="luthien_proxy.main"):
            load_config_from_env()

        warnings = [r for r in caplog.records if r.name == "luthien_proxy.main" and r.levelno == logging.WARNING]
        assert any("PROXY_API_KEY" in r.getMessage() for r in warnings), (
            "expected a warning pointing operators at the rename"
        )

    def test_auth_mode_flows_into_create_app(self, policy_config_file, mock_db_pool, mock_redis_client):
        """create_app with auth_mode=CLIENT_KEY initialises a CredentialManager."""
        app = create_app(
            api_key="sk-test-proxy-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.CLIENT_KEY,
        )

        with TestClient(app):
            assert app.state.dependencies.credential_manager is not None


# ---------------------------------------------------------------------------
# TestAuthModeClientKey
# ---------------------------------------------------------------------------


class TestAuthModeClientKey:
    """Verify that client_key mode enforces CLIENT_API_KEY-only access."""

    @pytest.fixture
    def client_key_app(self, policy_config_file, mock_db_pool, mock_redis_client):
        """App configured in client_key auth mode."""
        return create_app(
            api_key="sk-test-proxy-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.CLIENT_KEY,
        )

    def test_client_key_mode_accepts_client_key(self, client_key_app):
        """Correct client key is accepted (auth does not return 401/403)."""
        with TestClient(client_key_app) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-test-proxy-key"},
            )

        assert response.status_code not in (401, 403)

    def test_client_key_mode_rejects_unknown_key(self, client_key_app):
        """A key that is not the client key is rejected with 401."""
        with TestClient(client_key_app) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-anthropic-real-key"},
            )

        assert response.status_code in (401, 403)

    def test_client_key_mode_rejects_missing_auth(self, client_key_app):
        """Requests without an Authorization header are rejected with 401."""
        with TestClient(client_key_app) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
            )

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# TestAuthModeBoth
# ---------------------------------------------------------------------------


class TestAuthModeBoth:
    """Verify that 'both' mode accepts the client key and rejects missing auth."""

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

    def test_both_mode_accepts_client_key(self, both_mode_app):
        """The client key is accepted in 'both' mode (auth does not return 401/403)."""
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
# TestAuthWithNoClientKey — api_key=None scenarios
# ---------------------------------------------------------------------------


class TestAuthWithNoClientKey:
    """Verify auth behavior when CLIENT_API_KEY is not configured (api_key=None)."""

    @pytest.fixture
    def both_mode_no_key(self, policy_config_file, mock_db_pool, mock_redis_client):
        """App in both mode with no CLIENT_API_KEY — falls through to passthrough."""
        return create_app(
            api_key=None,
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.BOTH,
        )

    @pytest.fixture
    def passthrough_mode_no_key(self, policy_config_file, mock_db_pool, mock_redis_client):
        """App in passthrough mode with no CLIENT_API_KEY."""
        return create_app(
            api_key=None,
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.PASSTHROUGH,
        )

    def test_client_key_mode_refuses_to_start_without_key(self, policy_config_file, mock_db_pool, mock_redis_client):
        """client_key mode with no CLIENT_API_KEY refuses to start (hard error)."""
        app = create_app(
            api_key=None,
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.CLIENT_KEY,
        )
        with pytest.raises(RuntimeError, match="AUTH_MODE=client_key requires CLIENT_API_KEY"):
            with TestClient(app):
                pass

    def test_both_mode_falls_through_to_passthrough_when_no_key(self, both_mode_no_key):
        """both mode with no CLIENT_API_KEY skips client-key check, validates via passthrough."""
        with TestClient(both_mode_no_key, raise_server_exceptions=False) as client:
            # Patch validate_credential to return True (simulates valid passthrough cred)
            cm = client.app.state.dependencies.credential_manager
            cm.validate_credential = AsyncMock(return_value=True)

            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-ant-some-key"},
            )
        assert response.status_code not in (401, 403)
        cm.validate_credential.assert_called_once()

    def test_both_mode_rejects_invalid_passthrough_when_no_key(self, both_mode_no_key):
        """both mode with no CLIENT_API_KEY rejects credentials that fail validation."""
        with TestClient(both_mode_no_key) as client:
            cm = client.app.state.dependencies.credential_manager
            cm.validate_credential = AsyncMock(return_value=False)

            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-ant-bad-key"},
            )
        assert response.status_code in (401, 403)

    def test_passthrough_mode_validates_without_key(self, passthrough_mode_no_key):
        """passthrough mode validates credentials normally without CLIENT_API_KEY."""
        with TestClient(passthrough_mode_no_key, raise_server_exceptions=False) as client:
            cm = client.app.state.dependencies.credential_manager
            cm.validate_credential = AsyncMock(return_value=True)

            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
                headers={"Authorization": "Bearer sk-ant-some-key"},
            )
        assert response.status_code not in (401, 403)
        cm.validate_credential.assert_called_once()

    def test_missing_auth_rejected_when_no_key(self, both_mode_no_key):
        """Requests with no credentials are still rejected even without CLIENT_API_KEY."""
        with TestClient(both_mode_no_key) as client:
            response = client.post(
                "/v1/messages",
                json=_MINIMAL_BODY,
            )
        assert response.status_code in (401, 403)
