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

# Default upper bound on entries per policy namespace. Picked to be generous for
# realistic workloads while still providing a hard ceiling that prevents a leaky
# caller from filling the shared table. Override via constructor or factory.
DEFAULT_MAX_ENTRIES = 10_000

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

    A cap on entries per namespace is enforced on every ``put()``. When the
    cap is exceeded after an upsert, the oldest entries by ``created_at`` are
    evicted — FIFO, equivalent to LRU-by-insertion. This avoids the
    read-amplification cost of tracking last-access times (true LRU would
    turn every ``get()`` into a write) while still being predictable: a
    ``put()`` of a brand-new key cannot evict itself, because its own
    ``created_at`` is the largest in the namespace.

    The cap is *soft* under concurrent writers on Postgres: two concurrent
    puts to different keys in the same namespace at the cap can each race
    their count-and-evict step and leave the cache briefly over cap by up
    to (concurrent_writers - 1) entries. The next put into the same
    namespace will trim the excess, so the cache always converges back to
    the cap without unbounded growth. On SQLite the shim serializes all
    writes through a single connection, so the cap is effectively hard
    there.
    """

    def __init__(
        self,
        db_pool: DatabasePool,
        policy_name: str,
        max_entries: int | None = DEFAULT_MAX_ENTRIES,
    ) -> None:
        """Initialize cache for a specific policy.

        Args:
            db_pool: Database connection pool
            policy_name: Namespace for this policy's cache entries
            max_entries: Cap on entries for this policy_name (soft under
                concurrent writers on Postgres — see class docstring).
                ``None`` disables the cap (use sparingly — unbounded growth
                is the exact footgun this class exists to avoid). Must be
                positive if set. Defaults to :data:`DEFAULT_MAX_ENTRIES`.
        """
        if max_entries is not None and max_entries <= 0:
            raise ValueError(f"max_entries must be positive or None, got {max_entries}")
        self._db = db_pool
        self._policy_name = policy_name
        self._max_entries = max_entries

    @property
    def max_entries(self) -> int | None:
        """The configured entry cap for this cache namespace (``None`` = unbounded)."""
        return self._max_entries

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
        """Upsert a cache entry with the given TTL and enforce the size cap.

        Upsert and cap-enforcement run inside a single transaction so the cache
        never permanently exceeds its cap due to a racing put/evict pair, and
        callers do not need their own locks around get/put sequences.

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
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO policy_cache (policy_name, cache_key, value_json, expires_at) "
                    "VALUES ($1, $2, $3::jsonb, $4::timestamptz) "
                    "ON CONFLICT (policy_name, cache_key) DO UPDATE SET "
                    "value_json = EXCLUDED.value_json, "
                    "expires_at = EXCLUDED.expires_at",
                    self._policy_name,
                    key,
                    value_json,
                    expires_str,
                )
                if self._max_entries is not None:
                    await self._enforce_cap(conn, self._max_entries)

    async def _enforce_cap(self, conn: Any, cap: int) -> None:
        """Evict oldest-by-creation entries until the namespace fits within ``cap``.

        Must run inside a transaction on ``conn`` — callers are responsible
        for opening one so the count/delete pair is consistent with the
        preceding put. Under concurrent writers on Postgres this is still
        only a soft cap (see class docstring), but a convergent one: every
        subsequent put re-runs this check and trims any excess.

        Eviction order: ``ORDER BY created_at ASC, cache_key ASC``. The
        secondary sort on cache_key gives deterministic ordering when
        multiple rows share a created_at (identical DEFAULT NOW() within
        a single statement), which keeps tests stable and prevents
        concurrent evictors from picking different "oldest" sets.
        """
        count_raw = await conn.fetchval(
            "SELECT COUNT(*) FROM policy_cache WHERE policy_name = $1",
            self._policy_name,
        )
        assert isinstance(count_raw, int), f"unexpected COUNT(*) return type {type(count_raw).__name__}"
        excess = count_raw - cap
        if excess <= 0:
            return

        # SELECT-then-DELETE-in-a-loop keeps the shim's $N → ? rewrite simple
        # (it cannot dedupe a repeated $1 across a correlated subquery, so a
        # single DELETE...WHERE IN (SELECT...) is awkward to write portably).
        # The loop is O(excess) single-row deletes, and excess is 1 in the
        # steady state (one put, one eviction).
        victims = await conn.fetch(
            "SELECT cache_key FROM policy_cache WHERE policy_name = $1 ORDER BY created_at ASC, cache_key ASC LIMIT $2",
            self._policy_name,
            excess,
        )
        for row in victims:
            await conn.execute(
                "DELETE FROM policy_cache WHERE policy_name = $1 AND cache_key = $2",
                self._policy_name,
                row["cache_key"],
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


__all__ = ["PolicyCache", "PolicyCacheFactory", "DEFAULT_MAX_ENTRIES"]
