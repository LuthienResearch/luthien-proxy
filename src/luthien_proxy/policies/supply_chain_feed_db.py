"""Database access layer for supply chain feed policy.

All persistence goes through PoolProtocol — no direct asyncpg or
aiosqlite imports. Queries use asyncpg-style $1/$2 placeholders;
the SQLite translator handles the rest.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Mapping

    from luthien_proxy.utils.db import PoolProtocol

logger = logging.getLogger(__name__)


async def create_schema(pool: "PoolProtocol") -> None:
    """Create supply_chain_feed tables if they don't exist.

    Only used in tests with in-memory SQLite pools. Production uses migrations.
    """
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS supply_chain_feed (
            ecosystem    TEXT NOT NULL,
            name         TEXT NOT NULL,
            version      TEXT NOT NULL,
            cve_id       TEXT NOT NULL,
            severity     TEXT NOT NULL,
            published_at TEXT,
            modified_at  TEXT,
            fetched_at   TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (ecosystem, name, version, cve_id)
        )
        """
    )
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS supply_chain_feed_cursor (
            ecosystem          TEXT PRIMARY KEY,
            last_seen_modified TEXT,
            last_refreshed_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


async def upsert_entries(
    pool: "PoolProtocol",
    entries: "Sequence[tuple[str, str, str, str, str, datetime | None, datetime | None]]",
) -> int:
    """Upsert (ecosystem, name, version, cve_id, severity, published_at, modified_at) rows.

    Returns the number of rows upserted.
    """
    if not entries:
        return 0

    count = 0
    for eco, name, version, cve_id, severity, published_at, modified_at in entries:
        pub_str = published_at.isoformat() if published_at else None
        mod_str = modified_at.isoformat() if modified_at else None
        await pool.execute(
            """
            INSERT INTO supply_chain_feed
                (ecosystem, name, version, cve_id, severity, published_at, modified_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (ecosystem, name, version, cve_id) DO UPDATE SET
                severity = EXCLUDED.severity,
                modified_at = EXCLUDED.modified_at,
                fetched_at = EXCLUDED.fetched_at
            """,
            eco,
            name,
            version,
            cve_id,
            severity,
            pub_str,
            mod_str,
        )
        count += 1
    return count


async def load_all_entries(
    pool: "PoolProtocol",
) -> "Sequence[Mapping[str, object]]":
    """Load all entries from supply_chain_feed table."""
    return await pool.fetch("SELECT ecosystem, name, version, cve_id FROM supply_chain_feed")


async def get_cursor(pool: "PoolProtocol", ecosystem: str) -> datetime | None:
    """Get the last_seen_modified cursor for an ecosystem. Returns None if no cursor."""
    row = await pool.fetchrow(
        "SELECT last_seen_modified FROM supply_chain_feed_cursor WHERE ecosystem = $1",
        ecosystem,
    )
    if row is None:
        return None
    val = row["last_seen_modified"]
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        return datetime.fromisoformat(val)
    return None


async def set_cursor(pool: "PoolProtocol", ecosystem: str, last_seen_modified: datetime) -> None:
    """Set or update the cursor for an ecosystem."""
    mod_str = last_seen_modified.isoformat()
    now_str = datetime.now(timezone.utc).isoformat()
    await pool.execute(
        """
        INSERT INTO supply_chain_feed_cursor (ecosystem, last_seen_modified, last_refreshed_at)
        VALUES ($1, $2, $3)
        ON CONFLICT (ecosystem) DO UPDATE SET
            last_seen_modified = EXCLUDED.last_seen_modified,
            last_refreshed_at = EXCLUDED.last_refreshed_at
        """,
        ecosystem,
        mod_str,
        now_str,
    )


__all__ = [
    "create_schema",
    "get_cursor",
    "load_all_entries",
    "set_cursor",
    "upsert_entries",
]
