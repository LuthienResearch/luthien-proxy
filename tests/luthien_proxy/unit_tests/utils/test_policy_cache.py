"""Unit tests for PolicyCache — DB-backed policy caching."""

from __future__ import annotations

import pytest

from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.db_sqlite import SqlitePool
from luthien_proxy.utils.policy_cache import PolicyCache


@pytest.fixture
async def db_pool():
    """Create an in-memory SQLite pool with the policy_cache table."""
    pool = SqlitePool(":memory:")
    # Create the table manually (normally done by migrations)
    await pool.execute(
        "CREATE TABLE IF NOT EXISTS policy_cache ("
        "policy_name TEXT NOT NULL, "
        "cache_key TEXT NOT NULL, "
        "value_json TEXT NOT NULL, "
        "expires_at TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "PRIMARY KEY (policy_name, cache_key))"
    )
    yield pool
    await pool.close()


def _wrap_sqlite_pool(pool: SqlitePool) -> DatabasePool:
    """Wrap a SqlitePool in a DatabasePool for testing."""
    db = DatabasePool.__new__(DatabasePool)
    db._url = "sqlite:///:memory:"
    db._is_sqlite = True
    db._sqlite_pool = pool
    db._pool = None
    db._lock = None
    db._factory = None
    db._pool_kwargs = {}
    return db


@pytest.fixture
def cache(db_pool: SqlitePool):
    """Create a PolicyCache wrapping the in-memory pool."""
    return PolicyCache(_wrap_sqlite_pool(db_pool), "test_policy")


class TestPolicyCacheGetPut:
    @pytest.mark.asyncio
    async def test_put_and_get(self, cache: PolicyCache):
        await cache.put("key1", {"foo": "bar"}, ttl_seconds=3600)
        result = await cache.get("key1")
        assert result == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_get_miss(self, cache: PolicyCache):
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, cache: PolicyCache):
        await cache.put("key1", {"v": 1}, ttl_seconds=3600)
        await cache.put("key1", {"v": 2}, ttl_seconds=3600)
        result = await cache.get("key1")
        assert result == {"v": 2}

    @pytest.mark.asyncio
    async def test_expired_entry_returns_none(self, cache: PolicyCache):
        """Entry with negative TTL is immediately expired."""
        await cache.put("expired", {"data": True}, ttl_seconds=-1)
        result = await cache.get("expired")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, cache: PolicyCache):
        await cache.put("key1", {"v": 1}, ttl_seconds=3600)
        await cache.delete("key1")
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self, cache: PolicyCache):
        await cache.delete("nope")  # should not raise


class TestPolicyCacheJsonValues:
    @pytest.mark.asyncio
    async def test_put_and_get_list(self, cache: PolicyCache):
        """Cache accepts lists (not just dicts) and round-trips them."""
        await cache.put("items", [1, 2, {"nested": True}], ttl_seconds=3600)
        result = await cache.get("items")
        assert result == [1, 2, {"nested": True}]


class TestPolicyCacheIsolation:
    @pytest.mark.asyncio
    async def test_different_policies_isolated(self, db_pool: SqlitePool):
        """Two PolicyCache instances with different names don't see each other's entries."""
        db = _wrap_sqlite_pool(db_pool)

        cache_a = PolicyCache(db, "policy_a")
        cache_b = PolicyCache(db, "policy_b")

        await cache_a.put("shared_key", {"from": "a"}, ttl_seconds=3600)
        await cache_b.put("shared_key", {"from": "b"}, ttl_seconds=3600)

        result_a = await cache_a.get("shared_key")
        result_b = await cache_b.get("shared_key")

        assert result_a == {"from": "a"}
        assert result_b == {"from": "b"}


class TestPolicyCacheCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_expired(self, cache: PolicyCache):
        await cache.put("expired1", {"v": 1}, ttl_seconds=-1)
        await cache.put("expired2", {"v": 2}, ttl_seconds=-1)
        await cache.put("valid", {"v": 3}, ttl_seconds=3600)

        deleted = await cache.cleanup_expired()
        assert deleted == 2

        # Valid entry should still be there
        result = await cache.get("valid")
        assert result == {"v": 3}
