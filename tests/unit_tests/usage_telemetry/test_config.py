"""Tests for telemetry config resolution."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.usage_telemetry.config import resolve_telemetry_config


class TestResolveConfig:
    @pytest.mark.asyncio
    async def test_env_true_overrides_db_false(self):
        """Env var takes precedence over DB value."""
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(
            return_value={
                "enabled": False,
                "deployment_id": uuid.uuid4(),
            }
        )

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=True)
        assert config.enabled is True

    @pytest.mark.asyncio
    async def test_env_false_overrides_db_true(self):
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(
            return_value={
                "enabled": True,
                "deployment_id": uuid.uuid4(),
            }
        )

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=False)
        assert config.enabled is False

    @pytest.mark.asyncio
    async def test_no_env_uses_db_value(self):
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(
            return_value={
                "enabled": False,
                "deployment_id": uuid.uuid4(),
            }
        )

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=None)
        assert config.enabled is False

    @pytest.mark.asyncio
    async def test_no_env_no_db_defaults_enabled(self):
        """When nothing is configured, telemetry is enabled by default."""
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(
            return_value={
                "enabled": None,
                "deployment_id": uuid.uuid4(),
            }
        )

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=None)
        assert config.enabled is True

    @pytest.mark.asyncio
    async def test_deployment_id_from_db(self):
        dep_id = uuid.uuid4()
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(
            return_value={
                "enabled": None,
                "deployment_id": dep_id,
            }
        )

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=None)
        assert config.deployment_id == str(dep_id)

    @pytest.mark.asyncio
    async def test_no_db_pool_defaults_enabled_with_random_id(self):
        config = await resolve_telemetry_config(db_pool=None, env_value=None)
        assert config.enabled is True
        assert config.deployment_id  # non-empty string
