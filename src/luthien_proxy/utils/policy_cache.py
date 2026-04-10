"""DB-backed key-value cache for policies.

Provides persistent, cross-request caching scoped by policy name.
Used by policies that need cache entries to survive restarts and
be shared across workers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


# Type alias for the factory function injected into PolicyContext
type PolicyCacheFactory = Callable[[str], PolicyCache]


class PolicyCache:
    """DB-backed key-value cache scoped to a single policy.

    Each policy gets its own namespace via policy_name, preventing
    key collisions between different policies sharing the same table.
    """

    def __init__(self, db_pool: DatabasePool, policy_name: str) -> None:
        """Initialize cache for a specific policy.

        Args:
            db_pool: Database connection pool
            policy_name: Namespace for this policy's cache entries
        """
        self._db = db_pool
        self._policy_name = policy_name

    async def get(self, key: str) -> dict[str, Any] | None:
        """Get a cached value if it exists and hasn't expired.

        Returns None on cache miss or expired entry.
        """
        pool = await self._db.get_pool()

        if self._db.is_sqlite:
            now_expr = "datetime('now')"
        else:
            now_expr = "NOW()"

        row = await pool.fetchrow(
            f"SELECT value_json FROM policy_cache "
            f"WHERE policy_name = $1 AND cache_key = $2 AND expires_at > {now_expr}",
            self._policy_name,
            key,
        )
        if row is None:
            return None

        raw = row["value_json"]
        if isinstance(raw, str):
            return json.loads(raw)
        if isinstance(raw, dict):
            return raw
        return json.loads(str(raw))

    async def put(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        """Upsert a cache entry with the given TTL.

        Args:
            key: Cache key (unique within this policy's namespace)
            value: JSON-serializable dict to cache
            ttl_seconds: Time-to-live in seconds
        """
        pool = await self._db.get_pool()
        value_json = json.dumps(value)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        # Format compatible with both Postgres (accepts ISO) and SQLite (needs space separator, no tz)
        expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S") if self._db.is_sqlite else expires_at.isoformat()

        if self._db.is_sqlite:
            # SQLite uses positional ? params — can't reuse $3/$4, so pass them twice
            await pool.execute(
                "INSERT INTO policy_cache (policy_name, cache_key, value_json, expires_at, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, datetime('now'), datetime('now')) "
                "ON CONFLICT (policy_name, cache_key) DO UPDATE SET "
                "value_json = $5, expires_at = $6, updated_at = datetime('now')",
                self._policy_name,
                key,
                value_json,
                expires_str,
                value_json,
                expires_str,
            )
        else:
            await pool.execute(
                "INSERT INTO policy_cache (policy_name, cache_key, value_json, expires_at) "
                "VALUES ($1, $2, $3::jsonb, $4::timestamptz) "
                "ON CONFLICT (policy_name, cache_key) DO UPDATE SET "
                "value_json = EXCLUDED.value_json, expires_at = EXCLUDED.expires_at, updated_at = NOW()",
                self._policy_name,
                key,
                value_json,
                expires_str,
            )

    async def delete(self, key: str) -> None:
        """Remove a cache entry."""
        pool = await self._db.get_pool()
        await pool.execute(
            "DELETE FROM policy_cache WHERE policy_name = $1 AND cache_key = $2",
            self._policy_name,
            key,
        )

    async def cleanup_expired(self) -> int:
        """Delete all expired entries for this policy. Returns count deleted."""
        pool = await self._db.get_pool()
        if self._db.is_sqlite:
            result = await pool.execute(
                "DELETE FROM policy_cache WHERE policy_name = $1 AND expires_at <= datetime('now')",
                self._policy_name,
            )
        else:
            result = await pool.execute(
                "DELETE FROM policy_cache WHERE policy_name = $1 AND expires_at <= NOW()",
                self._policy_name,
            )
        count_str = str(result).rsplit(" ", 1)[-1]
        try:
            return int(count_str)
        except ValueError:
            return 0


__all__ = ["PolicyCache", "PolicyCacheFactory"]
