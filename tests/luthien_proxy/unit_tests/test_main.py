# ABOUTME: Unit tests for V2 main FastAPI application factory function
# ABOUTME: Tests create_app factory, app initialization, lifespan, and endpoint configuration

"""Tests for V2 main FastAPI application."""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.credential_manager import AuthMode
from luthien_proxy.main import auto_provision_defaults, connect_db, connect_redis, create_app, load_config_from_env


class TestLoadConfigFromEnv:
    """Test load_config_from_env function for environment variable validation.

    These tests pass a Settings instance with _env_file=None to bypass .env
    file loading and test validation logic in isolation.
    """

    def test_missing_admin_api_key_is_allowed(self, monkeypatch):
        """Test that missing ADMIN_API_KEY does not crash — admin endpoints
        handle the None gracefully at request time instead."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)

        config = load_config_from_env(settings=Settings(_env_file=None))
        assert config["admin_key"] is None

    def test_missing_proxy_api_key_is_allowed(self, monkeypatch):
        """PROXY_API_KEY is optional — passthrough mode doesn't need it."""
        from luthien_proxy.settings import Settings

        monkeypatch.delenv("PROXY_API_KEY", raising=False)
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

        config = load_config_from_env(settings=Settings(_env_file=None))
        assert config["api_key"] is None

    def test_missing_database_url_raises_error(self, monkeypatch):
        """Test that missing DATABASE_URL raises ValueError."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(ValueError, match="DATABASE_URL environment variable required"):
            load_config_from_env(settings=Settings(_env_file=None))

    def test_missing_database_url_reported(self, monkeypatch):
        """Test that missing DATABASE_URL is reported."""
        from luthien_proxy.settings import Settings

        monkeypatch.delenv("PROXY_API_KEY", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)

        with pytest.raises(ValueError, match="Missing required configuration") as exc_info:
            load_config_from_env(settings=Settings(_env_file=None))

        message = str(exc_info.value)
        assert "DATABASE_URL" in message

    def test_valid_config_returns_dict(self, monkeypatch):
        """Test that valid configuration returns expected dictionary."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
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

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("GATEWAY_PORT", "3000")

        config = load_config_from_env(settings=Settings(_env_file=None))

        assert config["gateway_port"] == 3000

    def test_empty_policy_config_returns_none(self, monkeypatch):
        """Test that empty POLICY_CONFIG returns None for startup_policy_path."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
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

    def test_does_not_provision_proxy_api_key(self, monkeypatch):
        monkeypatch.delenv("PROXY_API_KEY", raising=False)

        result = auto_provision_defaults()

        assert "PROXY_API_KEY" not in result
        assert "PROXY_API_KEY" not in os.environ

    def test_provisions_policy_config_when_missing(self, monkeypatch):
        monkeypatch.delenv("POLICY_CONFIG", raising=False)

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
        monkeypatch.setenv("DATABASE_URL", "postgresql://existing")
        monkeypatch.setenv("PROXY_API_KEY", "sk-existing")
        monkeypatch.setenv("ADMIN_API_KEY", "admin-existing")
        monkeypatch.setenv("POLICY_CONFIG", "custom/path.yaml")
        monkeypatch.setenv("POLICY_SOURCE", "db")

        result = auto_provision_defaults()

        assert result == {}
        assert os.environ["DATABASE_URL"] == "postgresql://existing"
        assert os.environ["PROXY_API_KEY"] == "sk-existing"
        assert os.environ["POLICY_CONFIG"] == "custom/path.yaml"
        assert os.environ["POLICY_SOURCE"] == "db"

    def test_provisions_multiple_missing_vars(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("PROXY_API_KEY", raising=False)
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)
        monkeypatch.delenv("POLICY_CONFIG", raising=False)
        monkeypatch.delenv("POLICY_SOURCE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        result = auto_provision_defaults()

        assert len(result) == 4
        assert all(k in result for k in ("DATABASE_URL", "ADMIN_API_KEY", "POLICY_CONFIG", "POLICY_SOURCE"))
        assert "PROXY_API_KEY" not in result


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
    mock = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock.get_pool = AsyncMock(return_value=mock_pool)
    mock.close = AsyncMock()
    mock.is_sqlite = False

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)

    from contextlib import asynccontextmanager

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
        assert app.version == "2.0.0"
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
        """Test health endpoint returns correct response shape with checks."""
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
            assert data["status"] == "healthy"
            assert data["version"] == "2.0.0"
            assert data["auth_mode"] in ("proxy_key", "both", "passthrough", None)
            assert "last_credential_type" in data
            assert "last_credential_at" in data
            assert "checks" in data
            assert data["checks"]["db"]["status"] == "ok"
            assert "latency_ms" in data["checks"]["db"]
            assert data["checks"]["redis"]["status"] == "ok"
            assert "latency_ms" in data["checks"]["redis"]

    def test_create_app_health_endpoint_proxy_key_mode(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Health endpoint reports auth_mode=proxy_key when configured."""
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.PROXY_KEY,
        )

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.json()["auth_mode"] == "proxy_key"

    def test_create_app_health_endpoint_both_mode(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Health endpoint reports auth_mode=both when configured."""
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.BOTH,
        )

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.json()["auth_mode"] == "both"

    def test_create_app_health_endpoint_passthrough_mode(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Health endpoint reports auth_mode=passthrough when configured."""
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
            auth_mode=AuthMode.PASSTHROUGH,
        )

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.json()["auth_mode"] == "passthrough"

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


class TestHealthChecks:
    def test_healthy_db_and_redis(self, policy_config_file, mock_db_pool, mock_redis_client):
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )
        with TestClient(app) as client:
            data = client.get("/health").json()
            assert data["status"] == "healthy"
            assert data["checks"]["db"]["status"] == "ok"
            assert isinstance(data["checks"]["db"]["latency_ms"], (int, float))
            assert data["checks"]["redis"]["status"] == "ok"
            assert isinstance(data["checks"]["redis"]["latency_ms"], (int, float))

    def test_db_error_returns_unhealthy(self, policy_config_file, mock_db_pool, mock_redis_client):
        mock_redis_client.get = AsyncMock(return_value=None)
        mock_db_pool._mock_conn.execute = AsyncMock(side_effect=RuntimeError("connection refused"))

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
            assert data["status"] == "unhealthy"
            assert data["checks"]["db"]["status"] == "error"
            assert "connection refused" in data["checks"]["db"]["error"]
            assert data["checks"]["redis"]["status"] == "ok"

    def test_redis_error_returns_degraded(self, policy_config_file, mock_db_pool, mock_redis_client):
        mock_redis_client.get = AsyncMock(return_value=None)
        mock_redis_client.ping = AsyncMock(side_effect=ConnectionError("redis down"))

        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )
        with TestClient(app) as client:
            data = client.get("/health").json()
            assert data["status"] == "degraded"
            assert data["checks"]["db"]["status"] == "ok"
            assert data["checks"]["redis"]["status"] == "error"
            assert "redis down" in data["checks"]["redis"]["error"]

    def test_both_not_configured_returns_healthy(self, policy_config_file, mock_db_pool, mock_redis_client):
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )
        with TestClient(app) as client:
            app.state.dependencies.db_pool = None
            app.state.dependencies.redis_client = None
            data = client.get("/health").json()
            assert data["status"] == "healthy"
            assert data["checks"]["db"]["status"] == "not_configured"
            assert data["checks"]["redis"]["status"] == "not_configured"

    def test_db_error_and_redis_error_returns_unhealthy(self, policy_config_file, mock_db_pool, mock_redis_client):
        mock_redis_client.get = AsyncMock(return_value=None)
        mock_db_pool._mock_conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
        mock_redis_client.ping = AsyncMock(side_effect=ConnectionError("redis down"))

        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )
        with TestClient(app) as client:
            data = client.get("/health").json()
            assert data["status"] == "unhealthy"
            assert data["checks"]["db"]["status"] == "error"
            assert data["checks"]["redis"]["status"] == "error"

    def test_preserves_existing_fields(self, policy_config_file, mock_db_pool, mock_redis_client):
        mock_redis_client.get = AsyncMock(return_value=None)
        app = create_app(
            api_key="test-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )
        with TestClient(app) as client:
            data = client.get("/health").json()
            assert "version" in data
            assert "auth_mode" in data
            assert "last_credential_type" in data
            assert "last_credential_at" in data
            assert "checks" in data
