# ABOUTME: Unit tests for Dependencies container and FastAPI dependency functions
# ABOUTME: Tests DI container creation, lazy event_publisher, and Depends() functions

"""Tests for dependency injection module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from luthien_proxy.dependencies import (
    Dependencies,
    get_admin_key,
    get_api_key,
    get_db_pool,
    get_dependencies,
    get_event_publisher,
    get_llm_client,
    get_policy,
    get_policy_manager,
    get_redis_client,
)
from luthien_proxy.llm.client import LLMClient
from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.utils import db


class MockLLMClient(LLMClient):
    """Mock LLM client for testing."""

    async def stream(self, request):
        """Mock stream."""
        yield MagicMock()

    async def complete(self, request):
        """Mock complete."""
        return MagicMock()


class TestDependenciesContainer:
    """Test Dependencies dataclass."""

    def test_dependencies_creation(self):
        """Test creating Dependencies container with all fields."""
        mock_db_pool = MagicMock(spec=db.DatabasePool)
        mock_redis = MagicMock(spec=Redis)
        mock_llm = MockLLMClient()
        mock_policy_manager = MagicMock(spec=PolicyManager)
        mock_policy_manager.current_policy = NoOpPolicy()

        deps = Dependencies(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            llm_client=mock_llm,
            policy_manager=mock_policy_manager,
            api_key="test-api-key",
            admin_key="test-admin-key",
        )

        assert deps.db_pool is mock_db_pool
        assert deps.redis_client is mock_redis
        assert deps.llm_client is mock_llm
        assert deps.policy_manager is mock_policy_manager
        assert deps.api_key == "test-api-key"
        assert deps.admin_key == "test-admin-key"

    def test_dependencies_with_none_values(self):
        """Test creating Dependencies with None for optional fields."""
        mock_llm = MockLLMClient()
        mock_policy_manager = MagicMock(spec=PolicyManager)
        mock_policy_manager.current_policy = NoOpPolicy()

        deps = Dependencies(
            db_pool=None,
            redis_client=None,
            llm_client=mock_llm,
            policy_manager=mock_policy_manager,
            api_key="test-key",
            admin_key=None,
        )

        assert deps.db_pool is None
        assert deps.redis_client is None
        assert deps.admin_key is None

    def test_event_publisher_derived_from_redis(self):
        """Test that event_publisher is lazily created from redis_client."""
        mock_redis = MagicMock(spec=Redis)
        mock_llm = MockLLMClient()
        mock_policy_manager = MagicMock(spec=PolicyManager)
        mock_policy_manager.current_policy = NoOpPolicy()

        deps = Dependencies(
            db_pool=None,
            redis_client=mock_redis,
            llm_client=mock_llm,
            policy_manager=mock_policy_manager,
            api_key="test-key",
            admin_key=None,
        )

        # Access event_publisher - should be created lazily
        publisher = deps.event_publisher
        assert publisher is not None
        assert isinstance(publisher, RedisEventPublisher)

        # Should return same instance on subsequent access (cached_property)
        publisher2 = deps.event_publisher
        assert publisher is publisher2

    def test_event_publisher_none_when_no_redis(self):
        """Test that event_publisher is None when redis_client is None."""
        mock_llm = MockLLMClient()
        mock_policy_manager = MagicMock(spec=PolicyManager)
        mock_policy_manager.current_policy = NoOpPolicy()

        deps = Dependencies(
            db_pool=None,
            redis_client=None,
            llm_client=mock_llm,
            policy_manager=mock_policy_manager,
            api_key="test-key",
            admin_key=None,
        )

        assert deps.event_publisher is None

    def test_policy_property_returns_current_policy(self):
        """Test that policy property returns current_policy from policy_manager."""
        mock_llm = MockLLMClient()
        mock_policy_manager = MagicMock(spec=PolicyManager)
        expected_policy = NoOpPolicy()
        mock_policy_manager.current_policy = expected_policy

        deps = Dependencies(
            db_pool=None,
            redis_client=None,
            llm_client=mock_llm,
            policy_manager=mock_policy_manager,
            api_key="test-key",
            admin_key=None,
        )

        assert deps.policy is expected_policy


class TestFastAPIDependsFunctions:
    """Test FastAPI Depends() functions."""

    @pytest.fixture
    def app_with_dependencies(self):
        """Create a FastAPI app with dependencies set up."""
        app = FastAPI()

        mock_db_pool = MagicMock(spec=db.DatabasePool)
        mock_redis = MagicMock(spec=Redis)
        mock_llm = MockLLMClient()
        mock_policy_manager = MagicMock(spec=PolicyManager)
        mock_policy_manager.current_policy = NoOpPolicy()

        deps = Dependencies(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            llm_client=mock_llm,
            policy_manager=mock_policy_manager,
            api_key="test-api-key",
            admin_key="test-admin-key",
        )

        app.state.dependencies = deps

        return app, deps

    @pytest.fixture
    def app_without_dependencies(self):
        """Create a FastAPI app without dependencies."""
        return FastAPI()

    def test_get_dependencies_returns_container(self, app_with_dependencies):
        """Test get_dependencies returns the Dependencies container."""
        app, expected_deps = app_with_dependencies

        @app.get("/test")
        def test_endpoint(deps: Dependencies = Depends(get_dependencies)):
            return {"has_deps": deps is not None}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["has_deps"] is True

    def test_get_dependencies_raises_when_not_initialized(self, app_without_dependencies):
        """Test get_dependencies raises HTTPException when not initialized."""
        app = app_without_dependencies

        @app.get("/test")
        def test_endpoint(deps: Dependencies = Depends(get_dependencies)):
            return {"deps": deps}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 500
            assert "not initialized" in response.json()["detail"]

    def test_get_db_pool(self, app_with_dependencies):
        """Test get_db_pool returns database pool."""
        app, deps = app_with_dependencies

        @app.get("/test")
        def test_endpoint(pool=Depends(get_db_pool)):
            return {"has_pool": pool is not None}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["has_pool"] is True

    def test_get_redis_client(self, app_with_dependencies):
        """Test get_redis_client returns redis client."""
        app, deps = app_with_dependencies

        @app.get("/test")
        def test_endpoint(redis=Depends(get_redis_client)):
            return {"has_redis": redis is not None}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["has_redis"] is True

    def test_get_llm_client(self, app_with_dependencies):
        """Test get_llm_client returns LLM client."""
        app, deps = app_with_dependencies

        @app.get("/test")
        def test_endpoint(llm=Depends(get_llm_client)):
            return {"has_llm": llm is not None}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["has_llm"] is True

    def test_get_event_publisher(self, app_with_dependencies):
        """Test get_event_publisher returns event publisher."""
        app, deps = app_with_dependencies

        @app.get("/test")
        def test_endpoint(publisher=Depends(get_event_publisher)):
            return {"has_publisher": publisher is not None}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["has_publisher"] is True

    def test_get_policy(self, app_with_dependencies):
        """Test get_policy returns current policy."""
        app, deps = app_with_dependencies

        @app.get("/test")
        def test_endpoint(policy=Depends(get_policy)):
            return {"policy_type": type(policy).__name__}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["policy_type"] == "NoOpPolicy"

    def test_get_policy_manager(self, app_with_dependencies):
        """Test get_policy_manager returns policy manager."""
        app, deps = app_with_dependencies

        @app.get("/test")
        def test_endpoint(manager=Depends(get_policy_manager)):
            return {"has_manager": manager is not None}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["has_manager"] is True

    def test_get_api_key(self, app_with_dependencies):
        """Test get_api_key returns API key."""
        app, deps = app_with_dependencies

        @app.get("/test")
        def test_endpoint(key=Depends(get_api_key)):
            return {"key": key}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["key"] == "test-api-key"

    def test_get_admin_key(self, app_with_dependencies):
        """Test get_admin_key returns admin key."""
        app, deps = app_with_dependencies

        @app.get("/test")
        def test_endpoint(key=Depends(get_admin_key)):
            return {"key": key}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["key"] == "test-admin-key"


class TestDependenciesIntegrationWithMain:
    """Test that Dependencies integrates correctly with main.py create_app."""

    def test_dependencies_container_in_app_state(self, policy_config_file):
        """Test that create_app properly sets up Dependencies container."""
        from unittest.mock import patch

        from luthien_proxy.main import create_app

        with (
            patch("luthien_proxy.main.db.DatabasePool") as mock_db_pool_class,
            patch("luthien_proxy.main.Redis") as mock_redis_class,
            patch("luthien_proxy.main.setup_telemetry"),
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

            app = create_app(
                api_key="test-api-key",
                admin_key="test-admin-key",
                database_url="postgresql://test:test@localhost/test",
                redis_url="redis://localhost:6379",
                policy_source="file",
                policy_config_path=policy_config_file,
            )

            with TestClient(app):
                # Verify dependencies container exists
                assert hasattr(app.state, "dependencies")
                assert isinstance(app.state.dependencies, Dependencies)

                # Verify all fields are set
                deps = app.state.dependencies
                assert deps.db_pool is not None
                assert deps.redis_client is not None
                assert deps.llm_client is not None
                assert deps.policy_manager is not None
                assert deps.api_key == "test-api-key"
                assert deps.admin_key == "test-admin-key"

                # Verify event_publisher is derived correctly
                assert deps.event_publisher is not None
                assert isinstance(deps.event_publisher, RedisEventPublisher)

    @pytest.fixture
    def policy_config_file(self):
        """Create a temporary policy config file for testing."""
        import tempfile
        from pathlib import Path

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
