"""Unit tests for PolicyManager.

Tests cover:
1. PolicyManager initialization
2. Loading policy from database
3. Loading policy from YAML file
4. Policy hot-swapping via enable_policy
5. get_current_policy metadata retrieval
6. Distributed locking behavior
7. Troubleshooting generation
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_manager import PolicyEnableResult, PolicyInfo, PolicyManager


class TestPolicyEnableResult:
    """Test PolicyEnableResult dataclass."""

    def test_success_result(self):
        """Test creating a success result."""
        result = PolicyEnableResult(
            success=True,
            policy="luthien_proxy.policies.noop_policy:NoOpPolicy",
            restart_duration_ms=150,
        )
        assert result.success is True
        assert result.policy is not None
        assert result.error is None

    def test_failure_result(self):
        """Test creating a failure result."""
        result = PolicyEnableResult(
            success=False,
            error="Import error",
            troubleshooting=["Check module path", "Verify policy exists"],
        )
        assert result.success is False
        assert result.error == "Import error"
        assert result.troubleshooting is not None
        assert len(result.troubleshooting) == 2


class TestPolicyInfo:
    """Test PolicyInfo dataclass."""

    def test_policy_info_creation(self):
        """Test creating PolicyInfo."""
        info = PolicyInfo(
            policy="NoOpPolicy",
            class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            enabled_at="2024-01-01T12:00:00",
            enabled_by="admin",
            config={},
        )
        assert info.policy == "NoOpPolicy"
        assert info.enabled_by == "admin"


class TestPolicyManagerInit:
    """Test PolicyManager initialization."""

    def test_init_without_startup_path(self):
        """Test initialization without startup policy path."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        assert manager.db == mock_pool
        assert manager.redis == mock_redis
        assert manager.startup_policy_path is None
        assert manager._current_policy is None

    def test_init_with_startup_path(self):
        """Test initialization with startup policy path."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(
            db_pool=mock_pool,
            redis_client=mock_redis,
            startup_policy_path="/path/to/policy.yaml",
        )

        assert manager.startup_policy_path == "/path/to/policy.yaml"


class TestPolicyManagerInitialize:
    """Test PolicyManager initialize() method."""

    @pytest.mark.asyncio
    async def test_initialize_from_db_success(self):
        """Test successful initialization from database."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_connection_pool = AsyncMock()
        mock_pool.get_pool = AsyncMock(return_value=mock_connection_pool)

        # Mock database response
        mock_connection_pool.fetchrow = AsyncMock(
            return_value={
                "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
                "config": {},
            }
        )

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)
        await manager.initialize()

        assert manager._current_policy is not None
        assert isinstance(manager._current_policy, NoOpPolicy)

    @pytest.mark.asyncio
    async def test_initialize_no_policy_raises_error(self):
        """Test that initialize raises error when no policy found."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_connection_pool = AsyncMock()
        mock_pool.get_pool = AsyncMock(return_value=mock_connection_pool)

        # No policy in database
        mock_connection_pool.fetchrow = AsyncMock(return_value=None)

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        with pytest.raises(RuntimeError, match="No policy configured"):
            await manager.initialize()

    @pytest.mark.asyncio
    async def test_initialize_from_file_success(self):
        """Test successful initialization from YAML file."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_connection_pool = AsyncMock()
        mock_pool.get_pool = AsyncMock(return_value=mock_connection_pool)
        mock_connection_pool.execute = AsyncMock()

        # Create a temporary YAML file
        yaml_content = """
policy:
  class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
  config: {}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = f.name

        try:
            manager = PolicyManager(
                db_pool=mock_pool,
                redis_client=mock_redis,
                startup_policy_path=yaml_path,
            )
            await manager.initialize()

            assert manager._current_policy is not None
            # Should have persisted to DB
            mock_connection_pool.execute.assert_called()
        finally:
            os.unlink(yaml_path)

    @pytest.mark.asyncio
    async def test_initialize_from_file_not_found(self):
        """Test that missing file raises FileNotFoundError."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(
            db_pool=mock_pool,
            redis_client=mock_redis,
            startup_policy_path="/nonexistent/policy.yaml",
        )

        with pytest.raises(FileNotFoundError):
            await manager.initialize()


class TestPolicyManagerLoadFromDb:
    """Test _load_from_db method."""

    @pytest.mark.asyncio
    async def test_load_from_db_with_json_string_config(self):
        """Test loading policy when config is a JSON string."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_connection_pool = AsyncMock()
        mock_pool.get_pool = AsyncMock(return_value=mock_connection_pool)

        # Config as JSON string (as it might come from some DB drivers)
        mock_connection_pool.fetchrow = AsyncMock(
            return_value={
                "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
                "config": json.dumps({}),
            }
        )

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)
        policy = await manager._load_from_db()

        assert policy is not None

    @pytest.mark.asyncio
    async def test_load_from_db_returns_none_on_exception(self):
        """Test that _load_from_db returns None on exception."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_pool.get_pool = AsyncMock(side_effect=Exception("DB error"))

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)
        policy = await manager._load_from_db()

        assert policy is None


class TestPolicyManagerEnablePolicy:
    """Test enable_policy method."""

    @pytest.mark.asyncio
    async def test_enable_policy_success(self):
        """Test successful policy enable."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_connection_pool = AsyncMock()
        mock_pool.get_pool = AsyncMock(return_value=mock_connection_pool)
        mock_connection_pool.execute = AsyncMock()

        # Mock the lock
        mock_lock = AsyncMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.release = AsyncMock()
        mock_redis.lock = MagicMock(return_value=mock_lock)

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        result = await manager.enable_policy(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
            enabled_by="test",
        )

        assert result.success is True
        assert result.policy is not None
        assert manager._current_policy is not None

    @pytest.mark.asyncio
    async def test_enable_policy_invalid_class(self):
        """Test enable_policy with invalid class reference."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        # Mock the lock
        mock_lock = AsyncMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.release = AsyncMock()
        mock_redis.lock = MagicMock(return_value=mock_lock)

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        result = await manager.enable_policy(
            policy_class_ref="nonexistent.module:NonexistentPolicy",
            config={},
            enabled_by="test",
        )

        assert result.success is False
        assert result.error is not None
        assert result.troubleshooting is not None

    @pytest.mark.asyncio
    async def test_enable_policy_lock_not_acquired(self):
        """Test enable_policy when lock cannot be acquired."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        # Mock lock that fails to acquire
        mock_lock = AsyncMock()
        mock_lock.acquire = AsyncMock(return_value=False)
        mock_redis.lock = MagicMock(return_value=mock_lock)

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        with pytest.raises(HTTPException) as exc_info:
            await manager.enable_policy(
                policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
                config={},
                enabled_by="test",
            )

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_enable_policy_cleans_up_old_policy(self):
        """Test that old policy's on_session_end is called."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_connection_pool = AsyncMock()
        mock_pool.get_pool = AsyncMock(return_value=mock_connection_pool)
        mock_connection_pool.execute = AsyncMock()

        # Mock the lock
        mock_lock = AsyncMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.release = AsyncMock()
        mock_redis.lock = MagicMock(return_value=mock_lock)

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        # Set up an old policy with on_session_end
        old_policy = MagicMock()
        old_policy.on_session_end = AsyncMock()
        manager._current_policy = old_policy

        await manager.enable_policy(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
            enabled_by="test",
        )

        old_policy.on_session_end.assert_called_once()


class TestPolicyManagerGetCurrentPolicy:
    """Test get_current_policy method."""

    @pytest.mark.asyncio
    async def test_get_current_policy_no_policy(self):
        """Test get_current_policy raises when no policy loaded."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        with pytest.raises(RuntimeError, match="No policy loaded"):
            await manager.get_current_policy()

    @pytest.mark.asyncio
    async def test_get_current_policy_success(self):
        """Test get_current_policy returns policy info."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_connection_pool = AsyncMock()
        mock_pool.get_pool = AsyncMock(return_value=mock_connection_pool)

        # Mock DB metadata
        mock_connection_pool.fetchrow = AsyncMock(
            return_value={
                "enabled_at": datetime(2024, 1, 1, 12, 0, 0),
                "enabled_by": "admin",
            }
        )

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)
        manager._current_policy = NoOpPolicy()

        info = await manager.get_current_policy()

        assert info.policy == "NoOpPolicy"
        assert "NoOpPolicy" in info.class_ref
        assert info.enabled_at is not None
        assert info.enabled_by == "admin"

    @pytest.mark.asyncio
    async def test_get_current_policy_handles_db_error(self):
        """Test get_current_policy handles DB errors gracefully."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_pool.get_pool = AsyncMock(side_effect=Exception("DB error"))

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)
        manager._current_policy = NoOpPolicy()

        # Should not raise, just have None metadata
        info = await manager.get_current_policy()

        assert info.policy == "NoOpPolicy"
        assert info.enabled_at is None
        assert info.enabled_by is None


class TestPolicyManagerCurrentPolicyProperty:
    """Test current_policy property."""

    def test_current_policy_raises_when_none(self):
        """Test current_policy raises RuntimeError when no policy loaded."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        with pytest.raises(RuntimeError, match="No policy loaded"):
            _ = manager.current_policy

    def test_current_policy_returns_policy(self):
        """Test current_policy returns the policy when loaded."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)
        manager._current_policy = NoOpPolicy()

        policy = manager.current_policy
        assert isinstance(policy, NoOpPolicy)


class TestGenerateTroubleshooting:
    """Test _generate_troubleshooting method."""

    def test_import_error_troubleshooting(self):
        """Test troubleshooting for import errors."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        error = ImportError("No module named 'nonexistent'")
        tips = manager._generate_troubleshooting(error)

        assert any("module" in tip.lower() or "import" in tip.lower() for tip in tips)

    def test_database_error_troubleshooting(self):
        """Test troubleshooting for database errors."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        error = Exception("database connection refused")
        tips = manager._generate_troubleshooting(error)

        assert any("database" in tip.lower() for tip in tips)

    def test_file_error_troubleshooting(self):
        """Test troubleshooting for file errors."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        error = FileNotFoundError("No such file: policy.yaml")
        tips = manager._generate_troubleshooting(error)

        assert any("file" in tip.lower() or "yaml" in tip.lower() for tip in tips)

    def test_generic_error_troubleshooting(self):
        """Test troubleshooting for generic errors."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        error = Exception("something went wrong")
        tips = manager._generate_troubleshooting(error)

        assert len(tips) > 0
        assert any("logs" in tip.lower() for tip in tips)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
