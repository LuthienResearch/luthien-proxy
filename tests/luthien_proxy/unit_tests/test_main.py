# ABOUTME: Unit tests for V2 main FastAPI application factory function
# ABOUTME: Tests create_app factory, app initialization, lifespan, and endpoint configuration

"""Tests for V2 main FastAPI application."""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.credential_manager import AuthMode
from luthien_proxy.main import (
    auto_provision_defaults,
    connect_db,
    connect_redis,
    create_app,
    load_config_from_env,
    propagate_cli_overrides_to_env,
)


class TestLoadConfigFromEnv:
    """Test load_config_from_env function for environment variable validation.

    These tests pass a Settings instance with _env_file=None to bypass .env
    file loading and test validation logic in isolation.
    """

    def test_missing_admin_api_key_is_allowed(self, monkeypatch):
        """Test that missing ADMIN_API_KEY does not crash — admin endpoints
        handle the None gracefully at request time instead."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("CLIENT_API_KEY", "test-proxy-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)

        config = load_config_from_env(settings=Settings(_env_file=None))
        assert config["admin_key"] is None

    def test_missing_client_api_key_is_allowed(self, monkeypatch):
        """CLIENT_API_KEY is optional — passthrough mode doesn't need it."""
        from luthien_proxy.settings import Settings

        monkeypatch.delenv("CLIENT_API_KEY", raising=False)
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

        config = load_config_from_env(settings=Settings(_env_file=None))
        assert config["api_key"] is None

    def test_missing_database_url_raises_error(self, monkeypatch):
        """Test that missing DATABASE_URL raises ValueError."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("CLIENT_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(ValueError, match="DATABASE_URL environment variable required"):
            load_config_from_env(settings=Settings(_env_file=None))

    def test_missing_database_url_reported(self, monkeypatch):
        """Test that missing DATABASE_URL is reported."""
        from luthien_proxy.settings import Settings

        monkeypatch.delenv("CLIENT_API_KEY", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)

        with pytest.raises(ValueError, match="Missing required configuration") as exc_info:
            load_config_from_env(settings=Settings(_env_file=None))

        message = str(exc_info.value)
        assert "DATABASE_URL" in message

    def test_valid_config_returns_dict(self, monkeypatch):
        """Test that valid configuration returns expected dictionary."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("CLIENT_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6380")
        monkeypatch.setenv("POLICY_CONFIG", "custom/path.yaml")
        monkeypatch.delenv("GATEWAY_PORT", raising=False)  # ensure default (8000) is used

        config = load_config_from_env(settings=Settings(_env_file=None))

        assert config["api_key"] == "test-proxy-key"
        assert config["admin_key"] == "test-admin-key"
        assert config["database_url"] == "postgresql://test:test@localhost/test"
        assert config["redis_url"] == "redis://localhost:6380"
        assert config["startup_policy_path"] == "custom/path.yaml"
        assert config["gateway_port"] == 8000  # default value

    def test_gateway_port_from_env(self, monkeypatch):
        """Test that GATEWAY_PORT is included in config from environment."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("CLIENT_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("GATEWAY_PORT", "3000")

        config = load_config_from_env(settings=Settings(_env_file=None))

        assert config["gateway_port"] == 3000

    def test_empty_policy_config_returns_none(self, monkeypatch):
        """Test that empty POLICY_CONFIG returns None for startup_policy_path."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("CLIENT_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.delenv("POLICY_CONFIG", raising=False)

        config = load_config_from_env(settings=Settings(_env_file=None))

        assert config["startup_policy_path"] is None


class TestAutoProvisionDefaults:
    """Test auto_provision_defaults for fresh PaaS deployments."""

    def test_provisions_database_url_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert "DATABASE_URL" in result
        assert result["DATABASE_URL"].startswith("sqlite:///")
        assert os.environ["DATABASE_URL"] == result["DATABASE_URL"]

    def test_does_not_provision_client_api_key(self, monkeypatch):
        monkeypatch.delenv("CLIENT_API_KEY", raising=False)

        result = auto_provision_defaults()

        assert "CLIENT_API_KEY" not in result
        assert "CLIENT_API_KEY" not in os.environ

    def test_provisions_policy_config_when_missing(self, monkeypatch):
        monkeypatch.delenv("POLICY_CONFIG", raising=False)
        monkeypatch.delenv("RAILWAY_SERVICE_NAME", raising=False)

        result = auto_provision_defaults()

        assert result["POLICY_CONFIG"] == "config/policy_config.yaml"
        assert os.environ["POLICY_CONFIG"] == "config/policy_config.yaml"

    def test_provisions_policy_source_when_missing(self, monkeypatch):
        monkeypatch.delenv("POLICY_SOURCE", raising=False)

        result = auto_provision_defaults()

        assert result["POLICY_SOURCE"] == "file"
        assert os.environ["POLICY_SOURCE"] == "file"

    def test_provisions_admin_api_key_when_missing(self, monkeypatch):
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)

        result = auto_provision_defaults()

        assert "ADMIN_API_KEY" in result
        assert result["ADMIN_API_KEY"].startswith("admin-")
        assert os.environ["ADMIN_API_KEY"] == result["ADMIN_API_KEY"]

    def test_does_not_override_existing_values(self, monkeypatch):
        monkeypatch.delenv("RAILWAY_SERVICE_NAME", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://existing")
        monkeypatch.setenv("CLIENT_API_KEY", "sk-existing")
        monkeypatch.setenv("ADMIN_API_KEY", "admin-existing")
        monkeypatch.setenv("POLICY_CONFIG", "custom/path.yaml")
        monkeypatch.setenv("POLICY_SOURCE", "db")

        result = auto_provision_defaults()

        assert result == {}
        assert os.environ["DATABASE_URL"] == "postgresql://existing"
        assert os.environ["CLIENT_API_KEY"] == "sk-existing"
        assert os.environ["POLICY_CONFIG"] == "custom/path.yaml"
        assert os.environ["POLICY_SOURCE"] == "db"

    def test_provisions_multiple_missing_vars(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("CLIENT_API_KEY", raising=False)
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)
        monkeypatch.delenv("POLICY_CONFIG", raising=False)
        monkeypatch.delenv("POLICY_SOURCE", raising=False)
        monkeypatch.delenv("RAILWAY_SERVICE_NAME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert len(result) == 4
        assert all(k in result for k in ("DATABASE_URL", "ADMIN_API_KEY", "POLICY_CONFIG", "POLICY_SOURCE"))
        assert "CLIENT_API_KEY" not in result


class TestAutoProvisionDefaultsRailway:
    """Test Railway-specific auto-provisioning behavior."""

    def _clear_env(self, monkeypatch):
        """Remove all vars that auto_provision_defaults might read."""
        for var in (
            "DATABASE_URL",
            "CLIENT_API_KEY",
            "ADMIN_API_KEY",
            "POLICY_CONFIG",
            "POLICY_SOURCE",
            "AUTH_MODE",
            "LOCALHOST_AUTH_BYPASS",
            "RAILWAY_SERVICE_NAME",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_railway_sets_passthrough_auth(self, monkeypatch, tmp_path):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("RAILWAY_SERVICE_NAME", "gateway")
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert result["AUTH_MODE"] == "passthrough"
        assert os.environ["AUTH_MODE"] == "passthrough"

    def test_railway_sets_railway_policy_config(self, monkeypatch, tmp_path):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("RAILWAY_SERVICE_NAME", "gateway")
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert result["POLICY_CONFIG"] == "config/railway_policy_config.yaml"

    def test_railway_disables_localhost_auth_bypass(self, monkeypatch, tmp_path):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("RAILWAY_SERVICE_NAME", "gateway")
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert result["LOCALHOST_AUTH_BYPASS"] == "false"
        assert os.environ["LOCALHOST_AUTH_BYPASS"] == "false"

    def test_railway_does_not_override_explicit_auth_mode(self, monkeypatch, tmp_path):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("RAILWAY_SERVICE_NAME", "gateway")
        monkeypatch.setenv("AUTH_MODE", "both")
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert "AUTH_MODE" not in result
        assert os.environ["AUTH_MODE"] == "both"

    def test_railway_does_not_override_explicit_policy_config(self, monkeypatch, tmp_path):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("RAILWAY_SERVICE_NAME", "gateway")
        monkeypatch.setenv("POLICY_CONFIG", "config/custom.yaml")
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert "POLICY_CONFIG" not in result
        assert os.environ["POLICY_CONFIG"] == "config/custom.yaml"

    def test_non_railway_does_not_set_auth_mode(self, monkeypatch, tmp_path):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert "AUTH_MODE" not in result
        assert "LOCALHOST_AUTH_BYPASS" not in result

    def test_non_railway_uses_default_policy_config(self, monkeypatch, tmp_path):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert result["POLICY_CONFIG"] == "config/policy_config.yaml"


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

    # Cleanup
    Path(config_path).unlink(missing_ok=True)


@pytest.fixture
def mock_db_pool():
    """Create a mock database pool for testing."""
    from contextlib import asynccontextmanager

    mock = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock.get_pool = AsyncMock(return_value=mock_pool)
    mock.close = AsyncMock()
    mock.is_sqlite = False

    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=1)

    @asynccontextmanager
    async def _connection():
        yield mock_conn

    mock.connection = _connection
    mock._mock_conn = mock_conn
    return mock


@pytest.fixture
def mock_redis_client():
    """Create a mock Redis client for testing."""
    mock = AsyncMock()
    mock.ping = AsyncMock()
    mock.close = AsyncMock()
    return mock


class TestCreateApp:
    """Test create_app factory function."""

    @pytest.mark.asyncio
    async def test_create_app_basic(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Test basic app creation with minimal config."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        assert app.title == "Luthien Proxy Gateway"
        assert app.version and app.version != "unknown"
        assert app.description == "Multi-provider LLM proxy with integrated control plane"

    @pytest.mark.asyncio
    async def test_create_app_lifespan_initialization(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Test that lifespan properly initializes app.state."""
        app = create_app(
            api_key="test-api-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        # Use TestClient to trigger lifespan
        with TestClient(app):
            # Verify dependencies container is set up
            from luthien_proxy.dependencies import Dependencies

            assert hasattr(app.state, "dependencies")
            assert isinstance(app.state.dependencies, Dependencies)

            # Verify all dependencies are properly initialized via container
            deps = app.state.dependencies
            assert deps.api_key == "test-api-key"
            assert deps.policy_manager is not None
            assert deps.db_pool == mock_db_pool
            assert deps.redis_client == mock_redis_client

        # create_app does NOT close db_pool/redis_client - caller owns them
        mock_db_pool.close.assert_not_called()
        mock_redis_client.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_app_routes_included(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Test that all expected routes are included."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        routes = [getattr(route, "path", None) for route in app.routes]

        # Check for key routes
        assert "/health" in routes
        assert "/" in routes
        # Gateway routes (from gateway_router) - only /v1/messages
        assert "/v1/messages" in routes or any("/v1/messages" in str(r) for r in routes if r)

    def test_create_app_health_endpoint(self, policy_config_file, mock_db_pool, mock_redis_client):
        """/health is a dependency-free liveness probe: always {status, version} at 200.

        It must not probe DB/Redis (a liveness probe that fails on a DB blip
        would trigger pointless restarts) and must not leak auth_mode or
        credential activity to unauthenticated callers. Rich diagnostics live
        on /api/admin/system-status; billing signals on /api/admin/billing-status.
        """
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data == {"status": "healthy", "version": data["version"]}
            assert data["version"] and data["version"] != "unknown"
            # Liveness must not depend on or expose dependency state.
            assert "checks" not in data
            assert "auth_mode" not in data
            assert "last_credential_type" not in data
            assert "last_credential_at" not in data

    def test_system_status_returns_403_without_auth(self, policy_config_file, mock_db_pool, mock_redis_client):
        """system-status is gated by admin auth — unauthenticated callers get 403.

        This is the whole point of moving the rich checks off /health: the
        timing/topology signals are not exposed to unauthenticated probes.
        """
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            assert client.get("/api/admin/system-status").status_code == 403

    def test_system_status_healthy_when_both_ok(self, policy_config_file, mock_db_pool, mock_redis_client):
        """system-status reports healthy with per-component ok + latency when DB and Redis answer."""
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/admin/system-status",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert data["checks"]["db"]["status"] == "ok"
            assert data["checks"]["redis"]["status"] == "ok"
            assert data["checks"]["db"]["latency_ms"] is not None
            # Lock the probe query to match /ready's contract.
            mock_db_pool._mock_conn.fetchval.assert_called_with("SELECT 1")

    def test_system_status_unhealthy_when_db_error(self, policy_config_file, mock_db_pool, mock_redis_client):
        """DB probe failure → overall unhealthy; raw exception text is not leaked."""
        mock_db_pool._mock_conn.fetchval = AsyncMock(side_effect=Exception("connection refused: db.internal:5432"))
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/admin/system-status",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["checks"]["db"]["status"] == "error"
            assert data["checks"]["db"]["error"] == "database check failed"
            # Latency is populated even on the error path (useful for ops).
            assert data["checks"]["db"]["latency_ms"] is not None
            assert "db.internal" not in response.text

    def test_system_status_degraded_when_redis_error(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Redis probe failure with DB ok → overall degraded (not unhealthy)."""
        mock_redis_client.ping = AsyncMock(side_effect=Exception("timeout"))
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            data = client.get(
                "/api/admin/system-status",
                headers={"Authorization": "Bearer test-admin-key"},
            ).json()
            assert data["status"] == "degraded"
            assert data["checks"]["db"]["status"] == "ok"
            assert data["checks"]["redis"]["status"] == "error"
            assert data["checks"]["redis"]["error"] == "redis check failed"

    def test_system_status_redis_not_configured(self, policy_config_file, mock_db_pool):
        """No Redis client → redis not_configured, overall still healthy."""
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=None,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            data = client.get(
                "/api/admin/system-status",
                headers={"Authorization": "Bearer test-admin-key"},
            ).json()
            assert data["status"] == "healthy"
            assert data["checks"]["db"]["status"] == "ok"
            assert data["checks"]["redis"]["status"] == "not_configured"

    def test_system_status_db_priority_over_redis(self, policy_config_file, mock_db_pool, mock_redis_client):
        """DB error takes priority over Redis error — overall is unhealthy, not degraded."""
        mock_db_pool._mock_conn.fetchval = AsyncMock(side_effect=Exception("db down"))
        mock_redis_client.ping = AsyncMock(side_effect=Exception("redis down"))
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            data = client.get(
                "/api/admin/system-status",
                headers={"Authorization": "Bearer test-admin-key"},
            ).json()
            assert data["status"] == "unhealthy"
            assert data["checks"]["db"]["status"] == "error"
            assert data["checks"]["redis"]["status"] == "error"

    def test_system_status_db_timeout_is_bounded(
        self, policy_config_file, mock_db_pool, mock_redis_client, monkeypatch
    ):
        """A hung DB connection is bounded by the probe timeout, not left to tarpit the request."""
        from contextlib import asynccontextmanager

        from luthien_proxy.admin import routes as admin_routes

        monkeypatch.setattr(admin_routes, "SYSTEM_STATUS_PROBE_TIMEOUT_SECONDS", 0.05)

        @asynccontextmanager
        async def hanging_connection():
            await asyncio.sleep(1.0)
            yield  # unreachable under the patched timeout

        mock_db_pool.connection = hanging_connection
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            data = client.get(
                "/api/admin/system-status",
                headers={"Authorization": "Bearer test-admin-key"},
            ).json()
            assert data["status"] == "unhealthy"
            assert data["checks"]["db"]["status"] == "error"
            assert data["checks"]["db"]["error"] == "database check timed out"

    def test_system_status_db_not_configured_is_unhealthy(self, policy_config_file, mock_db_pool, mock_redis_client):
        """DB is required: a missing db_pool is unhealthy, not healthy (unlike optional Redis)."""
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            app.state.dependencies.db_pool = None
            data = client.get(
                "/api/admin/system-status",
                headers={"Authorization": "Bearer test-admin-key"},
            ).json()
            assert data["status"] == "unhealthy"
            assert data["checks"]["db"]["status"] == "not_configured"
            assert data["checks"]["redis"]["status"] == "ok"

    def test_system_status_redis_timeout_is_bounded(
        self, policy_config_file, mock_db_pool, mock_redis_client, monkeypatch
    ):
        """A hung Redis ping is bounded by the probe timeout (symmetry with the DB probe)."""
        from luthien_proxy.admin import routes as admin_routes

        monkeypatch.setattr(admin_routes, "SYSTEM_STATUS_PROBE_TIMEOUT_SECONDS", 0.05)

        async def _hang(*args, **kwargs):
            await asyncio.sleep(1.0)

        mock_redis_client.ping = _hang
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            data = client.get(
                "/api/admin/system-status",
                headers={"Authorization": "Bearer test-admin-key"},
            ).json()
            # DB ok + Redis timed out → degraded (Redis is optional).
            assert data["status"] == "degraded"
            assert data["checks"]["redis"]["status"] == "error"
            assert data["checks"]["redis"]["error"] == "redis check timed out"

    def test_billing_status_returns_403_without_auth(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Billing status is gated by admin auth — unauthenticated callers get 403."""
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            response = client.get("/api/admin/billing-status")
            assert response.status_code == 403

    def test_billing_status_reports_auth_mode_client_key(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Billing status reports auth_mode=client_key when configured."""
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.CLIENT_KEY,
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/admin/billing-status",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert response.status_code == 200
            assert response.json()["auth_mode"] == "client_key"

    def test_billing_status_reports_auth_mode_both(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Billing status reports auth_mode=both when configured."""
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.BOTH,
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/admin/billing-status",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert response.json()["auth_mode"] == "both"

    def test_billing_status_reports_auth_mode_passthrough(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Billing status reports auth_mode=passthrough when configured."""
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.PASSTHROUGH,
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/admin/billing-status",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert response.json()["auth_mode"] == "passthrough"

    def test_billing_status_carries_last_credential_info_when_set(
        self, policy_config_file, mock_db_pool, mock_redis_client
    ):
        """Billing status surfaces last_credential_type/at when populated."""
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            app.state.dependencies.last_credential_info = {  # type: ignore[attr-defined]
                "type": "user_api_key",
                "timestamp": 1700000000.5,
            }
            response = client.get(
                "/api/admin/billing-status",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["last_credential_type"] == "user_api_key"
            assert body["last_credential_at"] == 1700000000.5

    def test_billing_status_handles_no_credential_manager(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Billing status returns auth_mode=None when credential_manager is unset."""
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            app.state.dependencies.credential_manager = None  # type: ignore[attr-defined]
            response = client.get(
                "/api/admin/billing-status",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert response.status_code == 200
            assert response.json()["auth_mode"] is None

    def test_billing_status_response_is_not_cacheable(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Billing status must not be cached — it can change at any time.

        The Cache-Control header comes from StaticCacheMiddleware's /api/*
        allowlist, not from the route itself. This test guards against a
        future refactor that moves the route off /api/* without re-adding
        the cache-control intent (or that drops the middleware allowlist).
        """
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/admin/billing-status",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"

    def test_ready_endpoint_returns_200_with_real_sqlite_pool(self, policy_config_file, mock_redis_client):
        """/ready returns 200 against a real SqlitePool — exercises the actual DB API.

        Uses an in-memory SqliteDatabasePool so the probe goes through the real
        `connection().fetchval` path. A mock-only test would not catch the case
        where the production code calls a method that does not exist on
        SqlitePool.
        """
        from luthien_proxy.utils import db as db_module

        real_pool = db_module.DatabasePool("sqlite://:memory:")
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=real_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            response = client.get("/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}
            assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"

    def test_ready_endpoint_returns_503_when_db_unreachable(self, policy_config_file, mock_db_pool, mock_redis_client):
        """/ready returns 503 with sanitized reason when the connection raises.

        No raw exception text or connection details may leak to unauthenticated
        callers.
        """
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def failing_connection():
            raise ConnectionRefusedError("conn refused: db.internal.example.com:5432")
            yield  # unreachable, required by @asynccontextmanager

        mock_db_pool.connection = failing_connection
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            response = client.get("/ready")
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "not_ready"
            assert body["reason"] == "database unreachable"
            assert "db.internal.example.com" not in response.text

    def test_ready_endpoint_returns_503_when_db_probe_times_out(
        self, policy_config_file, mock_db_pool, mock_redis_client, monkeypatch
    ):
        """/ready returns 503 when the probe exceeds the bounded timeout.

        A slow DB must not tarpit probe workers — the entire probe (pool
        acquisition + query) is bounded by asyncio.wait_for.
        """
        from contextlib import asynccontextmanager

        from luthien_proxy import main as main_module

        monkeypatch.setattr(main_module, "READY_DB_PROBE_TIMEOUT_SECONDS", 0.05)

        @asynccontextmanager
        async def hanging_connection():
            await asyncio.sleep(1.0)
            yield  # unreachable under the patched timeout

        mock_db_pool.connection = hanging_connection
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            response = client.get("/ready")
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "not_ready"
            assert body["reason"] == "database probe timed out"

    def test_ready_endpoint_returns_503_when_dependencies_not_initialized(
        self, policy_config_file, mock_db_pool, mock_redis_client
    ):
        """/ready returns 503 when app.state.dependencies is missing post-startup."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            app.state.dependencies = None  # type: ignore[attr-defined]
            response = client.get("/ready")
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "not_ready"
            assert body["reason"] == "dependencies not initialized"

    def test_create_app_root_endpoint(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Test root endpoint returns HTML landing page."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app) as client:
            response = client.get("/")
            assert response.status_code == 200
            # Verify it's HTML content
            assert response.headers["content-type"].startswith("text/html")
            # Basic sanity checks on content
            content = response.text
            assert "Luthien" in content

    @pytest.mark.asyncio
    async def test_create_app_with_custom_policy(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Test app creation with custom policy config."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app):
            # Verify the policy manager was initialized
            assert app.state.dependencies.policy_manager is not None

    def test_create_app_static_files_mounted(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Test that static files are properly mounted."""
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        # Check that /v2/static route exists
        routes = [getattr(route, "path", None) for route in app.routes]
        assert any("static" in str(r).lower() for r in routes if r)


class TestConnectDb:
    """Test connect_db function."""

    @pytest.mark.asyncio
    async def test_connect_db_success(self):
        """Test successful database connection."""
        from unittest.mock import AsyncMock, patch

        with patch("luthien_proxy.main.db.DatabasePool") as mock_pool_class:
            mock_pool = AsyncMock()
            mock_pool.get_pool = AsyncMock()
            mock_pool_class.return_value = mock_pool

            result = await connect_db("postgresql://test:test@localhost/test")

            assert result is mock_pool
            mock_pool_class.assert_called_once_with("postgresql://test:test@localhost/test")
            mock_pool.get_pool.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_db_failure_raises(self):
        """Test that connection failure raises RuntimeError."""
        from unittest.mock import AsyncMock, patch

        with patch("luthien_proxy.main.db.DatabasePool") as mock_pool_class:
            mock_pool = AsyncMock()
            mock_pool.get_pool = AsyncMock(side_effect=Exception("Connection failed"))
            mock_pool_class.return_value = mock_pool

            with pytest.raises(RuntimeError, match="Failed to connect to database"):
                await connect_db("postgresql://invalid:invalid@localhost/invalid")


class TestConnectRedis:
    """Test connect_redis function."""

    @pytest.mark.asyncio
    async def test_connect_redis_success(self):
        """Test successful Redis connection."""
        from unittest.mock import AsyncMock, patch

        with patch("luthien_proxy.main.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_redis_class.from_url.return_value = mock_client

            result = await connect_redis("redis://localhost:6379")

            assert result is mock_client
            mock_redis_class.from_url.assert_called_once_with("redis://localhost:6379", decode_responses=False)
            mock_client.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_redis_failure_raises(self):
        """Test that connection failure raises RuntimeError."""
        from unittest.mock import AsyncMock, patch

        with patch("luthien_proxy.main.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=Exception("Connection failed"))
            mock_redis_class.from_url.return_value = mock_client

            with pytest.raises(RuntimeError, match="Failed to connect to Redis"):
                await connect_redis("redis://invalid:6379")


class TestPropagateCliOverridesToEnv:
    """CLI flags for startup-critical fields must land in the env mapping.

    Otherwise the flow at startup — load_config_from_env → uvicorn.Config →
    connect_db — reads from get_settings() which only consults env, bypassing
    any cli_overrides dict that hasn't been synced back yet.
    """

    def test_int_field_propagated_as_decimal_string(self):
        env: dict[str, str] = {}
        propagate_cli_overrides_to_env({"gateway_port": 9999}, environ=env)
        assert env["GATEWAY_PORT"] == "9999"

    def test_bool_field_propagated_lowercase(self):
        env: dict[str, str] = {}
        propagate_cli_overrides_to_env({"dogfood_mode": True}, environ=env)
        assert env["DOGFOOD_MODE"] == "true"
        propagate_cli_overrides_to_env({"dogfood_mode": False}, environ=env)
        assert env["DOGFOOD_MODE"] == "false"

    def test_enum_field_propagated_as_value(self):
        env: dict[str, str] = {}
        propagate_cli_overrides_to_env({"auth_mode": AuthMode.PASSTHROUGH}, environ=env)
        assert env["AUTH_MODE"] == "passthrough"

    def test_str_field_propagated_verbatim(self):
        env: dict[str, str] = {}
        propagate_cli_overrides_to_env({"database_url": "postgresql://localhost/test"}, environ=env)
        assert env["DATABASE_URL"] == "postgresql://localhost/test"

    def test_cli_value_is_visible_via_get_settings_after_cache_clear(self, monkeypatch):
        """Regression test for the startup flag bug: CLI override for gateway_port
        must be visible to load_config_from_env() which reads via get_settings()."""
        from luthien_proxy.settings import clear_settings_cache, get_settings

        # monkeypatch.setenv tracks the var for automatic teardown cleanup.
        monkeypatch.setenv("GATEWAY_PORT", "9999")
        clear_settings_cache()
        try:
            assert get_settings().gateway_port == 9999
        finally:
            clear_settings_cache()
