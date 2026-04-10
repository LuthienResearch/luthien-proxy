"""DB-backed key-value cache for policies.

Provides persistent, cross-request caching scoped by policy name.
Used by policies that need cache entries to survive restarts and
be shared across workers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from luthien_proxy.utils.db import DatabasePool

# Type alias for the factory function injected into PolicyContext
type PolicyCacheFactory = Callable[[str], PolicyCache]

# SQLite stores expires_at as TEXT and compares it with datetime('now'), which
# relies on lexicographic ordering over the "YYYY-MM-DD HH:MM:SS" format.
# A space separator (not "T") is required for this to match datetime('now') output
# and keep the lex-compare invariant intact.
_SQLITE_EXPIRES_FORMAT = "%Y-%m-%d %H:%M:%S"
_SQLITE_EXPIRES_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


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

    async def get(self, key: str) -> Any:
        """Get a cached value if it exists and hasn't expired.

        Returns None on cache miss or expired entry. The returned value is
        whatever JSON-serializable value was stored (dict, list, scalar, etc.).
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
        # WHY: asyncpg may hand back JSONB as either an already-decoded dict/list
        # or a raw str depending on connection/codec configuration; SQLite's TEXT
        # column is always str. Mirror the dual-path pattern used by
        # request_log.service._parse_jsonb and debug.service so a Postgres
        # deployment with JSONB auto-decoding doesn't trip an assertion.
        if isinstance(raw, str):
            return json.loads(raw)
        if isinstance(raw, (dict, list)):
            return raw
        raise TypeError(f"unexpected value_json type {type(raw).__name__}")

    async def put(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Upsert a cache entry with the given TTL.

        The upsert is atomic (single SQL statement with ON CONFLICT), so callers
        do not need to add their own locks around get/put sequences.

        Args:
            key: Cache key (unique within this policy's namespace)
            value: Any JSON-serializable value (dict, list, scalar, etc.)
            ttl_seconds: Time-to-live in seconds
        """
        pool = await self._db.get_pool()
        value_json = json.dumps(value)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        if self._db.is_sqlite:
            # WHY: see _SQLITE_EXPIRES_FORMAT — must stay space-separated, no "T",
            # no timezone suffix, so lex-compare against datetime('now') stays valid.
            expires_str = expires_at.strftime(_SQLITE_EXPIRES_FORMAT)
            assert _SQLITE_EXPIRES_REGEX.match(expires_str), (
                f"SQLite expires_at format drift detected: {expires_str!r} — "
                "must match 'YYYY-MM-DD HH:MM:SS' for lex-compare invariant"
            )
        else:
            expires_str = expires_at.isoformat()

        # Single SQL works on both backends: _translate_params in db_sqlite.py
        # strips ::jsonb/::timestamptz casts and rewrites NOW() to datetime('now').
        # SQLite 3.24+ supports EXCLUDED.col in ON CONFLICT, so no dual SQL needed.
        await pool.execute(
            "INSERT INTO policy_cache (policy_name, cache_key, value_json, expires_at) "
            "VALUES ($1, $2, $3::jsonb, $4::timestamptz) "
            "ON CONFLICT (policy_name, cache_key) DO UPDATE SET "
            "value_json = EXCLUDED.value_json, "
            "expires_at = EXCLUDED.expires_at, "
            "updated_at = NOW()",
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
        """Delete all expired entries for this policy. Returns count deleted.

        Count and delete run inside a single transaction so the count is
        accurate even under concurrent cleanup calls.
        """
        pool = await self._db.get_pool()
        if self._db.is_sqlite:
            now_expr = "datetime('now')"
        else:
            now_expr = "NOW()"
        # Count-then-delete inside a transaction is portable across asyncpg and
        # the SQLite shim, and avoids parsing execute() result strings or relying
        # on DELETE RETURNING (which the SQLite shim's fetch() does not commit).
        async with pool.acquire() as conn:
            async with conn.transaction():
                count_raw = await conn.fetchval(
                    f"SELECT COUNT(*) FROM policy_cache WHERE policy_name = $1 AND expires_at <= {now_expr}",
                    self._policy_name,
                )
                await conn.execute(
                    f"DELETE FROM policy_cache WHERE policy_name = $1 AND expires_at <= {now_expr}",
                    self._policy_name,
                )
        # Both asyncpg and the SQLite shim return the column value (int) here;
        # the Protocol is typed as `object` to stay backend-agnostic.
        assert isinstance(count_raw, int), f"unexpected COUNT(*) return type {type(count_raw).__name__}"
        return count_raw


__all__ = ["PolicyCache", "PolicyCacheFactory"]
