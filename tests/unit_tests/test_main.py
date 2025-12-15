# ABOUTME: Unit tests for V2 main FastAPI application factory function
# ABOUTME: Tests create_app factory, app initialization, lifespan, and endpoint configuration

"""Tests for V2 main FastAPI application."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.main import connect_db, connect_redis, create_app, get_app, load_config_from_env


class TestLoadConfigFromEnv:
    """Test load_config_from_env function for environment variable validation.

    These tests pass a Settings instance with _env_file=None to bypass .env
    file loading and test validation logic in isolation.
    """

    def test_missing_admin_api_key_raises_error(self, monkeypatch):
        """Test that missing ADMIN_API_KEY raises ValueError."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)

        with pytest.raises(ValueError, match="ADMIN_API_KEY environment variable required"):
            load_config_from_env(settings=Settings(_env_file=None))

    def test_missing_proxy_api_key_raises_error(self, monkeypatch):
        """Test that missing PROXY_API_KEY raises ValueError."""
        from luthien_proxy.settings import Settings

        monkeypatch.delenv("PROXY_API_KEY", raising=False)
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

        with pytest.raises(ValueError, match="PROXY_API_KEY environment variable required"):
            load_config_from_env(settings=Settings(_env_file=None))

    def test_missing_database_url_raises_error(self, monkeypatch):
        """Test that missing DATABASE_URL raises ValueError."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(ValueError, match="DATABASE_URL environment variable required"):
            load_config_from_env(settings=Settings(_env_file=None))

    def test_valid_config_returns_dict(self, monkeypatch):
        """Test that valid configuration returns expected dictionary."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6380")
        monkeypatch.setenv("POLICY_CONFIG", "custom/path.yaml")

        config = load_config_from_env(settings=Settings(_env_file=None))

        assert config["api_key"] == "test-proxy-key"
        assert config["admin_key"] == "test-admin-key"
        assert config["database_url"] == "postgresql://test:test@localhost/test"
        assert config["redis_url"] == "redis://localhost:6380"
        assert config["startup_policy_path"] == "custom/path.yaml"

    def test_empty_policy_config_returns_none(self, monkeypatch):
        """Test that empty POLICY_CONFIG returns None for startup_policy_path."""
        from luthien_proxy.settings import Settings

        monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.delenv("POLICY_CONFIG", raising=False)

        config = load_config_from_env(settings=Settings(_env_file=None))

        assert config["startup_policy_path"] is None


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
    mock.get_pool = AsyncMock()
    mock.close = AsyncMock()
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
            assert deps.event_publisher is not None
            assert deps.llm_client is not None

        # create_app does NOT close db_pool/redis_client - caller owns them
        mock_db_pool.close.assert_not_called()
        mock_redis_client.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_app_no_database_raises_error(self, policy_config_file, mock_redis_client):
        """Test that app raises RuntimeError when database is None (PolicyManager requires DB)."""
        app = create_app(
            api_key="test-api-key",
            admin_key=None,
            db_pool=None,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        # App startup (lifespan) should raise RuntimeError since PolicyManager requires both DB and Redis
        with pytest.raises(RuntimeError, match="Database and Redis required for PolicyManager"):
            with TestClient(app):
                pass

    @pytest.mark.asyncio
    async def test_create_app_no_redis_raises_error(self, policy_config_file, mock_db_pool):
        """Test that app raises RuntimeError when Redis is None (PolicyManager requires Redis)."""
        app = create_app(
            api_key="test-api-key",
            admin_key=None,
            db_pool=mock_db_pool,
            redis_client=None,
            startup_policy_path=policy_config_file,
        )

        # App startup (lifespan) should raise RuntimeError since PolicyManager requires both DB and Redis
        with pytest.raises(RuntimeError, match="Database and Redis required for PolicyManager"):
            with TestClient(app):
                pass

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
        # Gateway routes (from gateway_router)
        assert "/v1/chat/completions" in routes or any("/v1/chat/completions" in str(r) for r in routes if r)
        assert "/v1/messages" in routes or any("/v1/messages" in str(r) for r in routes if r)

    def test_create_app_health_endpoint(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Test health endpoint returns correct response."""
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
    async def test_connect_db_failure_returns_none(self):
        """Test that connection failure returns None."""
        from unittest.mock import AsyncMock, patch

        with patch("luthien_proxy.main.db.DatabasePool") as mock_pool_class:
            mock_pool = AsyncMock()
            mock_pool.get_pool = AsyncMock(side_effect=Exception("Connection failed"))
            mock_pool_class.return_value = mock_pool

            result = await connect_db("postgresql://invalid:invalid@localhost/invalid")

            assert result is None


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
    async def test_connect_redis_failure_returns_none(self):
        """Test that connection failure returns None."""
        from unittest.mock import AsyncMock, patch

        with patch("luthien_proxy.main.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=Exception("Connection failed"))
            mock_redis_class.from_url.return_value = mock_client

            result = await connect_redis("redis://invalid:6379")

            assert result is None


class TestGetApp:
    """Test get_app function."""

    @pytest.mark.asyncio
    async def test_get_app_creates_app_with_connections(self, monkeypatch, policy_config_file):
        """Test that get_app creates app with DB and Redis connections."""
        from unittest.mock import AsyncMock, patch

        monkeypatch.setenv("PROXY_API_KEY", "test-key")
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        monkeypatch.setenv("POLICY_CONFIG", policy_config_file)

        mock_db_pool = AsyncMock()
        mock_redis_client = AsyncMock()

        with (
            patch("luthien_proxy.main.connect_db", return_value=mock_db_pool) as mock_connect_db,
            patch("luthien_proxy.main.connect_redis", return_value=mock_redis_client) as mock_connect_redis,
        ):
            from luthien_proxy.settings import Settings

            app = await get_app(settings=Settings(_env_file=None))

            mock_connect_db.assert_called_once_with("postgresql://test:test@localhost/test")
            mock_connect_redis.assert_called_once_with("redis://localhost:6379")
            assert app.title == "Luthien Proxy Gateway"
