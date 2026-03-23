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
    get_policy_manager,
    get_redis_client,
)
from luthien_proxy.observability.emitter import NullEventEmitter
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.utils import db


class TestDependenciesContainer:
    """Test Dependencies dataclass."""

    def test_dependencies_creation(self):
        """Test creating Dependencies container with all fields."""
        mock_db_pool = MagicMock(spec=db.DatabasePool)
        mock_redis = MagicMock(spec=Redis)
        mock_policy_manager = MagicMock(spec=PolicyManager)
        mock_policy_manager.current_policy = NoOpPolicy()
        mock_emitter = NullEventEmitter()

        deps = Dependencies(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            policy_manager=mock_policy_manager,
            emitter=mock_emitter,
            api_key="test-api-key",
            admin_key="test-admin-key",
        )

        assert deps.db_pool is mock_db_pool
        assert deps.redis_client is mock_redis
        assert deps.policy_manager is mock_policy_manager
        assert deps.emitter is mock_emitter
        assert deps.api_key == "test-api-key"
        assert deps.admin_key == "test-admin-key"

    def test_dependencies_with_none_values(self):
        """Test creating Dependencies with None for optional fields."""
        mock_policy_manager = MagicMock(spec=PolicyManager)
        mock_policy_manager.current_policy = NoOpPolicy()

        deps = Dependencies(
            db_pool=None,
            redis_client=None,
            policy_manager=mock_policy_manager,
            emitter=NullEventEmitter(),
            api_key="test-key",
            admin_key=None,
        )

        assert deps.db_pool is None
        assert deps.redis_client is None
        assert deps.admin_key is None


class TestFastAPIDependsFunctions:
    """Test FastAPI Depends() functions."""

    @pytest.fixture
    def app_with_dependencies(self):
        """Create a FastAPI app with dependencies set up."""
        app = FastAPI()

        mock_db_pool = MagicMock(spec=db.DatabasePool)
        mock_redis = MagicMock(spec=Redis)
        mock_policy_manager = MagicMock(spec=PolicyManager)
        mock_policy_manager.current_policy = NoOpPolicy()

        deps = Dependencies(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            policy_manager=mock_policy_manager,
            emitter=NullEventEmitter(),
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

    @pytest.fixture
    def mock_db_pool(self):
        """Create a mock database pool for testing."""
        mock = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock.get_pool = AsyncMock(return_value=mock_pool)
        mock.close = AsyncMock()
        mock.is_sqlite = False
        return mock

    @pytest.fixture
    def mock_redis_client(self):
        """Create a mock Redis client for testing."""
        mock = AsyncMock()
        mock.ping = AsyncMock()
        mock.close = AsyncMock()
        return mock

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

    def test_dependencies_container_in_app_state(self, policy_config_file, mock_db_pool, mock_redis_client):
        """Test that create_app properly sets up Dependencies container."""
        from luthien_proxy.main import create_app

        app = create_app(
            api_key="test-api-key",
            admin_key="test-admin-key",
            db_pool=mock_db_pool,
            redis_client=mock_redis_client,
            startup_policy_path=policy_config_file,
        )

        with TestClient(app):
            # Verify dependencies container exists
            assert hasattr(app.state, "dependencies")
            assert isinstance(app.state.dependencies, Dependencies)

            # Verify all fields are set
            deps = app.state.dependencies
            assert deps.db_pool is mock_db_pool
            assert deps.redis_client is mock_redis_client
            assert deps.policy_manager is not None
            assert deps.api_key == "test-api-key"
            assert deps.admin_key == "test-admin-key"
