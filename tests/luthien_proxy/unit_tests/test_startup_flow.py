# ABOUTME: Unit tests for application startup flow and lifespan error handling
# ABOUTME: Tests lifespan error propagation, dependency initialization, and policy source strategies

"""Tests for application startup flow."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.main import create_app, load_config_from_env


@pytest.fixture
def policy_config_file():
    """Create a temporary policy config file for testing."""
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
    """Create a mock database pool for testing."""
    mock = AsyncMock()
    mock_pool = AsyncMock()
    # fetchrow returns None by default (no rows found)
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock.get_pool = AsyncMock(return_value=mock_pool)
    mock.close = AsyncMock()
    mock.is_sqlite = False
    return mock


@pytest.fixture
def mock_redis_client():
    """Create a mock Redis client for testing."""
    mock = AsyncMock()
    mock.ping = AsyncMock()
    mock.close = AsyncMock()
    return mock


class TestLifespanErrorHandling:
    """Test that lifespan startup failures propagate correctly."""

    def test_lifespan_migration_failure_propagates(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Migration check failures must prevent startup."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with patch("luthien_proxy.main.check_migrations", side_effect=RuntimeError("Migrations are behind")):
            with pytest.raises(Exception):
                with TestClient(app):
                    pass

    def test_lifespan_policy_initialization_failure_propagates(
        self, policy_config_file, mock_db_pool, mock_redis_client
    ):
        """Policy load failures must prevent startup."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with patch(
            "luthien_proxy.main.PolicyManager.initialize",
            new_callable=lambda: lambda _self: AsyncMock(side_effect=RuntimeError("Policy load failed"))(),
        ):
            with pytest.raises(Exception):
                with TestClient(app):
                    pass

    def test_lifespan_happy_path_sets_all_required_dependencies(
        self, policy_config_file, mock_db_pool, mock_redis_client
    ):
        """All critical dependencies must be non-None after successful startup."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app):
            deps = app.state.dependencies
            assert deps.api_key is not None
            assert deps.policy_manager is not None
            assert deps.db_pool is not None
            assert deps.redis_client is not None
            assert deps.emitter is not None

    def test_lifespan_does_not_close_db_pool_on_shutdown(self, policy_config_file, mock_db_pool, mock_redis_client):
        """db_pool lifetime is owned by the caller, not the app lifespan."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app):
            pass

        mock_db_pool.close.assert_not_called()

    def test_lifespan_does_not_close_redis_on_shutdown(self, policy_config_file, mock_db_pool, mock_redis_client):
        """redis_client lifetime is owned by the caller, not the app lifespan."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app):
            pass

        mock_redis_client.close.assert_not_called()

    def test_lifespan_admin_key_stored_in_dependencies(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Admin key passed to create_app must be available in dependencies."""
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app):
            assert app.state.dependencies.admin_key == "test-admin-key"


class TestPolicySourceStartup:
    """Test that the policy_source parameter controls how policies are loaded at startup."""

    def test_policy_source_file_loads_yaml_successfully(self, policy_config_file, mock_db_pool, mock_redis_client):
        """policy_source='file' must load the policy from the YAML file."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            policy_source="file",
        )

        with TestClient(app):
            assert app.state.dependencies.policy_manager is not None

    def test_policy_source_db_fallback_file_with_empty_db(self, policy_config_file, mock_db_pool, mock_redis_client):
        """policy_source='db-fallback-file' with empty DB must fall back to YAML without error."""
        # mock_db_pool.get_pool().fetchrow returns None — simulates empty DB
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            policy_source="db-fallback-file",
        )

        with TestClient(app):
            assert app.state.dependencies.policy_manager is not None

    def test_policy_source_db_only_with_empty_db_fails(self, mock_db_pool, mock_redis_client):
        """policy_source='db' with no policy in DB must raise during startup."""
        # mock_db_pool returns fetchrow=None — no policy stored
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=None,
            policy_source="db",
        )

        with pytest.raises(Exception):
            with TestClient(app):
                pass

    def test_policy_source_env_var_flows_to_config(self, monkeypatch):
        """POLICY_SOURCE env var must be reflected in the config returned by load_config_from_env."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("POLICY_SOURCE", "file-fallback-db")

        config = load_config_from_env(settings=Settings(_env_file=None))  # type: ignore[call-arg]

        assert config["policy_source"] == "file-fallback-db"
