# ABOUTME: Unit tests for V2 main FastAPI application factory function
# ABOUTME: Tests create_app factory, app initialization, lifespan, and endpoint configuration

"""Tests for V2 main FastAPI application."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.main import create_app
from luthien_proxy.policies.noop_policy import NoOpPolicy


class TestCreateApp:
    """Test create_app factory function."""

    @pytest.mark.asyncio
    async def test_create_app_basic(self):
        """Test basic app creation with minimal config."""
        app = create_app(
            api_key="test-key",
            database_url="postgresql://test:test@localhost/test",
            redis_url="redis://localhost:6379",
            policy=NoOpPolicy(),
        )

        assert app.title == "Luthien Proxy Gateway"
        assert app.version == "2.0.0"
        assert app.description == "Multi-provider LLM proxy with integrated control plane"

    @pytest.mark.asyncio
    async def test_create_app_lifespan_initialization(self):
        """Test that lifespan properly initializes app.state."""
        policy = NoOpPolicy()
        app = create_app(
            api_key="test-api-key",
            database_url="postgresql://user:pass@localhost/db",
            redis_url="redis://localhost:6379",
            policy=policy,
        )

        # Mock dependencies to avoid real connections
        with (
            patch("luthien_proxy.main.db.DatabasePool") as mock_db_pool_class,
            patch("luthien_proxy.main.Redis") as mock_redis_class,
            patch("luthien_proxy.main.setup_telemetry") as mock_setup_telemetry,
        ):
            # Setup mocks
            mock_db_instance = AsyncMock()
            mock_db_instance.get_pool = AsyncMock()
            mock_db_instance.close = AsyncMock()
            mock_db_pool_class.return_value = mock_db_instance

            mock_redis_instance = AsyncMock()
            mock_redis_instance.ping = AsyncMock()
            mock_redis_instance.close = AsyncMock()
            mock_redis_class.from_url.return_value = mock_redis_instance

            # Use TestClient to trigger lifespan
            with TestClient(app):
                # Verify telemetry was setup
                mock_setup_telemetry.assert_called_once_with(app)

                # Verify app state was initialized
                assert app.state.api_key == "test-api-key"
                assert app.state.policy == policy
                assert app.state.db_pool == mock_db_instance
                assert app.state.redis_client == mock_redis_instance
                assert app.state.event_publisher is not None

            # Verify cleanup was called
            mock_db_instance.close.assert_called_once()
            mock_redis_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_app_database_failure_graceful(self):
        """Test that app handles database connection failure gracefully."""
        policy = NoOpPolicy()
        app = create_app(
            api_key="test-api-key",
            database_url="postgresql://invalid:invalid@localhost/invalid",
            redis_url="redis://localhost:6379",
            policy=policy,
        )

        with (
            patch("luthien_proxy.main.db.DatabasePool") as mock_db_pool_class,
            patch("luthien_proxy.main.Redis") as mock_redis_class,
            patch("luthien_proxy.main.setup_telemetry"),
        ):
            # Make DB connection fail
            mock_db_instance = AsyncMock()
            mock_db_instance.get_pool = AsyncMock(side_effect=Exception("DB connection failed"))
            mock_db_pool_class.return_value = mock_db_instance

            # Redis succeeds
            mock_redis_instance = AsyncMock()
            mock_redis_instance.ping = AsyncMock()
            mock_redis_instance.close = AsyncMock()
            mock_redis_class.from_url.return_value = mock_redis_instance

            with TestClient(app):
                # App should still start with db_pool = None
                assert app.state.db_pool is None
                assert app.state.redis_client == mock_redis_instance
                assert app.state.policy is not None

    @pytest.mark.asyncio
    async def test_create_app_redis_failure_graceful(self):
        """Test that app handles Redis connection failure gracefully."""
        policy = NoOpPolicy()
        app = create_app(
            api_key="test-api-key",
            database_url="postgresql://user:pass@localhost/db",
            redis_url="redis://invalid:6379",
            policy=policy,
        )

        with (
            patch("luthien_proxy.main.db.DatabasePool") as mock_db_pool_class,
            patch("luthien_proxy.main.Redis") as mock_redis_class,
            patch("luthien_proxy.main.setup_telemetry"),
        ):
            # DB succeeds
            mock_db_instance = AsyncMock()
            mock_db_instance.get_pool = AsyncMock()
            mock_db_instance.close = AsyncMock()
            mock_db_pool_class.return_value = mock_db_instance

            # Redis fails
            mock_redis_instance = AsyncMock()
            mock_redis_instance.ping = AsyncMock(side_effect=Exception("Redis connection failed"))
            mock_redis_class.from_url.return_value = mock_redis_instance

            with TestClient(app):
                # App should still start with redis_client = None
                assert app.state.db_pool == mock_db_instance
                assert app.state.redis_client is None
                assert app.state.event_publisher is None  # No publisher without Redis
                assert app.state.policy is not None

    @pytest.mark.asyncio
    async def test_create_app_routes_included(self):
        """Test that all expected routes are included."""
        app = create_app(
            api_key="test-key",
            database_url="postgresql://test:test@localhost/test",
            redis_url="redis://localhost:6379",
            policy=NoOpPolicy(),
        )

        routes = [getattr(route, "path", None) for route in app.routes]

        # Check for key routes
        assert "/health" in routes
        assert "/" in routes
        # Gateway routes (from gateway_router)
        assert "/v1/chat/completions" in routes or any("/v1/chat/completions" in str(r) for r in routes if r)
        assert "/v1/messages" in routes or any("/v1/messages" in str(r) for r in routes if r)

    def test_create_app_health_endpoint(self):
        """Test health endpoint returns correct response."""
        app = create_app(
            api_key="test-key",
            database_url="postgresql://test:test@localhost/test",
            redis_url="redis://localhost:6379",
            policy=NoOpPolicy(),
        )

        with (
            patch("luthien_proxy.main.db.DatabasePool"),
            patch("luthien_proxy.main.Redis"),
            patch("luthien_proxy.main.setup_telemetry"),
        ):
            with TestClient(app) as client:
                response = client.get("/health")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "healthy"
                assert data["version"] == "2.0.0"

    def test_create_app_root_endpoint(self):
        """Test root endpoint returns API information."""
        app = create_app(
            api_key="test-key",
            database_url="postgresql://test:test@localhost/test",
            redis_url="redis://localhost:6379",
            policy=NoOpPolicy(),
        )

        with (
            patch("luthien_proxy.main.db.DatabasePool"),
            patch("luthien_proxy.main.Redis"),
            patch("luthien_proxy.main.setup_telemetry"),
        ):
            with TestClient(app) as client:
                response = client.get("/")
                assert response.status_code == 200
                data = response.json()
                assert data["name"] == "Luthien Proxy Gateway"
                assert data["version"] == "2.0.0"
                assert "endpoints" in data
                assert data["endpoints"]["openai"] == "/v1/chat/completions"
                assert data["endpoints"]["anthropic"] == "/v1/messages"
                assert data["endpoints"]["health"] == "/health"

    @pytest.mark.asyncio
    async def test_create_app_with_custom_policy(self):
        """Test app creation with custom policy."""

        policy = NoOpPolicy()
        app = create_app(
            api_key="test-key",
            database_url="postgresql://test:test@localhost/test",
            redis_url="redis://localhost:6379",
            policy=policy,
        )

        with (
            patch("luthien_proxy.main.db.DatabasePool") as mock_db_pool_class,
            patch("luthien_proxy.main.Redis") as mock_redis_class,
            patch("luthien_proxy.main.setup_telemetry"),
        ):
            mock_db_instance = AsyncMock()
            mock_db_instance.get_pool = AsyncMock()
            mock_db_instance.close = AsyncMock()
            mock_db_pool_class.return_value = mock_db_instance

            mock_redis_instance = AsyncMock()
            mock_redis_instance.ping = AsyncMock()
            mock_redis_instance.close = AsyncMock()
            mock_redis_class.from_url.return_value = mock_redis_instance

            with TestClient(app):
                # Verify the control plane was initialized with our custom policy
                assert app.state.policy == policy

    def test_create_app_static_files_mounted(self):
        """Test that static files are properly mounted."""
        app = create_app(
            api_key="test-key",
            database_url="postgresql://test:test@localhost/test",
            redis_url="redis://localhost:6379",
            policy=NoOpPolicy(),
        )

        # Check that /v2/static route exists
        routes = [getattr(route, "path", None) for route in app.routes]
        assert any("static" in str(r).lower() for r in routes if r)
