"""Unit tests for PolicyManager.

Tests cover:
1. PolicyManager initialization and policy_source validation
2. Loading policy from database
3. Loading policy from YAML file
4. policy_source modes: db, file, db-fallback-file, file-fallback-db
5. Policy hot-swapping via enable_policy
6. get_current_policy metadata retrieval
7. Distributed locking behavior
8. Troubleshooting generation
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
from luthien_proxy.policy_manager import (
    VALID_POLICY_SOURCES,
    PolicyEnableResult,
    PolicyInfo,
    PolicyManager,
)

# -- Helpers ------------------------------------------------------------------

NOOP_CLASS_REF = "luthien_proxy.policies.noop_policy:NoOpPolicy"

NOOP_YAML = """\
policy:
  class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
  config: {}
"""


def _make_db_mocks(*, fetchrow_return=None, execute_ok=True):
    """Build mock db_pool and connection_pool wired together."""
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.get_pool = AsyncMock(return_value=mock_conn)
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    if execute_ok:
        mock_conn.execute = AsyncMock()
    return mock_pool, mock_conn


def _db_row_noop():
    return {"policy_class_ref": NOOP_CLASS_REF, "config": {}}


# -- Dataclass tests ----------------------------------------------------------


class TestPolicyEnableResult:
    def test_success_result(self):
        result = PolicyEnableResult(success=True, policy=NOOP_CLASS_REF, restart_duration_ms=150)
        assert result.success is True
        assert result.policy is not None
        assert result.error is None

    def test_failure_result(self):
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
    def test_policy_info_creation(self):
        info = PolicyInfo(
            policy="NoOpPolicy",
            class_ref=NOOP_CLASS_REF,
            enabled_at="2024-01-01T12:00:00",
            enabled_by="admin",
            config={},
        )
        assert info.policy == "NoOpPolicy"
        assert info.enabled_by == "admin"


# -- Init tests ---------------------------------------------------------------


class TestPolicyManagerInit:
    def test_init_defaults(self):
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        assert manager.db == mock_pool
        assert manager.redis == mock_redis
        assert manager.startup_policy_path is None
        assert manager.policy_source == "db-fallback-file"
        assert manager._current_policy is None

    def test_init_with_startup_path(self):
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        manager = PolicyManager(
            db_pool=mock_pool,
            redis_client=mock_redis,
            startup_policy_path="/path/to/policy.yaml",
        )
        assert manager.startup_policy_path == "/path/to/policy.yaml"

    def test_init_rejects_invalid_policy_source(self):
        with pytest.raises(ValueError, match="Invalid policy_source"):
            PolicyManager(db_pool=MagicMock(), redis_client=MagicMock(), policy_source="bogus")

    @pytest.mark.parametrize("source", sorted(VALID_POLICY_SOURCES))
    def test_init_accepts_valid_policy_sources(self, source: str):
        manager = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock(), policy_source=source)
        assert manager.policy_source == source


# -- policy_source="file" -----------------------------------------------------


class TestPolicySourceFile:
    @pytest.mark.asyncio
    async def test_file_loads_from_yaml_and_persists(self):
        mock_pool, mock_conn = _make_db_mocks()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(NOOP_YAML)
            yaml_path = f.name

        try:
            manager = PolicyManager(
                db_pool=mock_pool,
                redis_client=MagicMock(),
                startup_policy_path=yaml_path,
                policy_source="file",
            )
            await manager.initialize()

            assert isinstance(manager._current_policy, NoOpPolicy)
            mock_conn.execute.assert_called_once()
        finally:
            os.unlink(yaml_path)

    @pytest.mark.asyncio
    async def test_file_raises_when_file_missing(self):
        manager = PolicyManager(
            db_pool=MagicMock(),
            redis_client=MagicMock(),
            startup_policy_path="/nonexistent/policy.yaml",
            policy_source="file",
        )
        with pytest.raises(FileNotFoundError):
            await manager.initialize()

    @pytest.mark.asyncio
    async def test_file_raises_when_no_path_configured(self):
        manager = PolicyManager(
            db_pool=MagicMock(),
            redis_client=MagicMock(),
            policy_source="file",
        )
        with pytest.raises(FileNotFoundError):
            await manager.initialize()


# -- policy_source="db" -------------------------------------------------------


class TestPolicySourceDb:
    @pytest.mark.asyncio
    async def test_db_loads_from_database(self):
        mock_pool, _ = _make_db_mocks(fetchrow_return=_db_row_noop())

        manager = PolicyManager(db_pool=mock_pool, redis_client=MagicMock(), policy_source="db")
        await manager.initialize()

        assert isinstance(manager._current_policy, NoOpPolicy)

    @pytest.mark.asyncio
    async def test_db_raises_when_no_row(self):
        mock_pool, _ = _make_db_mocks(fetchrow_return=None)

        manager = PolicyManager(db_pool=mock_pool, redis_client=MagicMock(), policy_source="db")
        with pytest.raises(RuntimeError, match="POLICY_SOURCE=db but no policy found"):
            await manager.initialize()

    @pytest.mark.asyncio
    async def test_db_ignores_yaml_file(self):
        """Even when startup_policy_path is set, db mode only uses the database."""
        mock_pool, _ = _make_db_mocks(fetchrow_return=_db_row_noop())

        manager = PolicyManager(
            db_pool=mock_pool,
            redis_client=MagicMock(),
            startup_policy_path="/some/file.yaml",
            policy_source="db",
        )
        await manager.initialize()

        assert isinstance(manager._current_policy, NoOpPolicy)


# -- policy_source="db-fallback-file" (default) --------------------------------


class TestPolicySourceDbFallbackFile:
    @pytest.mark.asyncio
    async def test_uses_db_when_db_has_policy(self):
        mock_pool, mock_conn = _make_db_mocks(fetchrow_return=_db_row_noop())

        manager = PolicyManager(
            db_pool=mock_pool,
            redis_client=MagicMock(),
            startup_policy_path="/should/not/be/read.yaml",
            policy_source="db-fallback-file",
        )
        await manager.initialize()

        assert isinstance(manager._current_policy, NoOpPolicy)
        # Should NOT have called execute (no persist to DB since it loaded from DB)
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_file_when_db_empty(self):
        mock_pool, mock_conn = _make_db_mocks(fetchrow_return=None)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(NOOP_YAML)
            yaml_path = f.name

        try:
            manager = PolicyManager(
                db_pool=mock_pool,
                redis_client=MagicMock(),
                startup_policy_path=yaml_path,
                policy_source="db-fallback-file",
            )
            await manager.initialize()

            assert isinstance(manager._current_policy, NoOpPolicy)
            # File path loaded and persisted to DB
            mock_conn.execute.assert_called_once()
        finally:
            os.unlink(yaml_path)

    @pytest.mark.asyncio
    async def test_raises_when_both_db_and_file_fail(self):
        mock_pool, _ = _make_db_mocks(fetchrow_return=None)

        manager = PolicyManager(
            db_pool=mock_pool,
            redis_client=MagicMock(),
            startup_policy_path="/nonexistent.yaml",
            policy_source="db-fallback-file",
        )
        with pytest.raises(FileNotFoundError):
            await manager.initialize()


# -- policy_source="file-fallback-db" ------------------------------------------


class TestPolicySourceFileFallbackDb:
    @pytest.mark.asyncio
    async def test_uses_file_when_file_exists(self):
        mock_pool, mock_conn = _make_db_mocks()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(NOOP_YAML)
            yaml_path = f.name

        try:
            manager = PolicyManager(
                db_pool=mock_pool,
                redis_client=MagicMock(),
                startup_policy_path=yaml_path,
                policy_source="file-fallback-db",
            )
            await manager.initialize()

            assert isinstance(manager._current_policy, NoOpPolicy)
            mock_conn.execute.assert_called_once()
        finally:
            os.unlink(yaml_path)

    @pytest.mark.asyncio
    async def test_falls_back_to_db_when_file_missing(self):
        mock_pool, _ = _make_db_mocks(fetchrow_return=_db_row_noop())

        manager = PolicyManager(
            db_pool=mock_pool,
            redis_client=MagicMock(),
            startup_policy_path="/nonexistent.yaml",
            policy_source="file-fallback-db",
        )
        await manager.initialize()

        assert isinstance(manager._current_policy, NoOpPolicy)

    @pytest.mark.asyncio
    async def test_raises_when_both_file_and_db_fail(self):
        mock_pool, _ = _make_db_mocks(fetchrow_return=None)

        manager = PolicyManager(
            db_pool=mock_pool,
            redis_client=MagicMock(),
            startup_policy_path="/nonexistent.yaml",
            policy_source="file-fallback-db",
        )
        with pytest.raises(RuntimeError, match="No policy configured"):
            await manager.initialize()


# -- _load_from_db internals ---------------------------------------------------


class TestPolicyManagerLoadFromDb:
    @pytest.mark.asyncio
    async def test_load_from_db_with_json_string_config(self):
        mock_pool, mock_conn = _make_db_mocks(
            fetchrow_return={
                "policy_class_ref": NOOP_CLASS_REF,
                "config": json.dumps({}),
            }
        )

        manager = PolicyManager(db_pool=mock_pool, redis_client=MagicMock())
        policy = await manager._load_from_db()
        assert policy is not None

    @pytest.mark.asyncio
    async def test_load_from_db_returns_none_on_exception(self):
        mock_pool = MagicMock()
        mock_pool.get_pool = AsyncMock(side_effect=Exception("DB error"))

        manager = PolicyManager(db_pool=mock_pool, redis_client=MagicMock())
        policy = await manager._load_from_db()
        assert policy is None


# -- enable_policy -------------------------------------------------------------


class TestPolicyManagerEnablePolicy:
    @pytest.mark.asyncio
    async def test_enable_policy_success(self):
        mock_pool, _ = _make_db_mocks()
        mock_redis = MagicMock()

        mock_lock = AsyncMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.release = AsyncMock()
        mock_redis.lock = MagicMock(return_value=mock_lock)

        manager = PolicyManager(db_pool=mock_pool, redis_client=mock_redis)

        result = await manager.enable_policy(policy_class_ref=NOOP_CLASS_REF, config={}, enabled_by="test")

        assert result.success is True
        assert result.policy is not None
        assert manager._current_policy is not None

    @pytest.mark.asyncio
    async def test_enable_policy_invalid_class(self):
        mock_redis = MagicMock()
        mock_lock = AsyncMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.release = AsyncMock()
        mock_redis.lock = MagicMock(return_value=mock_lock)

        manager = PolicyManager(db_pool=MagicMock(), redis_client=mock_redis)

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
        mock_redis = MagicMock()
        mock_lock = AsyncMock()
        mock_lock.acquire = AsyncMock(return_value=False)
        mock_redis.lock = MagicMock(return_value=mock_lock)

        manager = PolicyManager(db_pool=MagicMock(), redis_client=mock_redis)

        with pytest.raises(HTTPException) as exc_info:
            await manager.enable_policy(policy_class_ref=NOOP_CLASS_REF, config={}, enabled_by="test")

        assert exc_info.value.status_code == 503


# -- get_current_policy --------------------------------------------------------


class TestPolicyManagerGetCurrentPolicy:
    @pytest.mark.asyncio
    async def test_get_current_policy_no_policy(self):
        manager = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())
        with pytest.raises(RuntimeError, match="No policy loaded"):
            await manager.get_current_policy()

    @pytest.mark.asyncio
    async def test_get_current_policy_success(self):
        mock_pool, mock_conn = _make_db_mocks(
            fetchrow_return={
                "enabled_at": datetime(2024, 1, 1, 12, 0, 0),
                "enabled_by": "admin",
            }
        )

        manager = PolicyManager(db_pool=mock_pool, redis_client=MagicMock())
        manager._current_policy = NoOpPolicy()

        info = await manager.get_current_policy()

        assert info.policy == "NoOpPolicy"
        assert "NoOpPolicy" in info.class_ref
        assert info.enabled_at is not None
        assert info.enabled_by == "admin"

    @pytest.mark.asyncio
    async def test_get_current_policy_handles_db_error(self):
        mock_pool = MagicMock()
        mock_pool.get_pool = AsyncMock(side_effect=Exception("DB error"))

        manager = PolicyManager(db_pool=mock_pool, redis_client=MagicMock())
        manager._current_policy = NoOpPolicy()

        info = await manager.get_current_policy()

        assert info.policy == "NoOpPolicy"
        assert info.enabled_at is None
        assert info.enabled_by is None


# -- current_policy property ---------------------------------------------------


class TestPolicyManagerCurrentPolicyProperty:
    def test_current_policy_raises_when_none(self):
        manager = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())
        with pytest.raises(RuntimeError, match="No policy loaded"):
            _ = manager.current_policy

    def test_current_policy_returns_policy(self):
        manager = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())
        manager._current_policy = NoOpPolicy()
        assert isinstance(manager.current_policy, NoOpPolicy)


# -- Troubleshooting -----------------------------------------------------------


class TestGenerateTroubleshooting:
    def test_import_error_troubleshooting(self):
        manager = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())
        tips = manager._generate_troubleshooting(ImportError("No module named 'nonexistent'"))
        assert any("module" in tip.lower() or "import" in tip.lower() for tip in tips)

    def test_database_error_troubleshooting(self):
        manager = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())
        tips = manager._generate_troubleshooting(Exception("database connection refused"))
        assert any("database" in tip.lower() for tip in tips)

    def test_file_error_troubleshooting(self):
        manager = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())
        tips = manager._generate_troubleshooting(FileNotFoundError("No such file: policy.yaml"))
        assert any("file" in tip.lower() or "yaml" in tip.lower() for tip in tips)

    def test_generic_error_troubleshooting(self):
        manager = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())
        tips = manager._generate_troubleshooting(Exception("something went wrong"))
        assert len(tips) > 0
        assert any("logs" in tip.lower() for tip in tips)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
