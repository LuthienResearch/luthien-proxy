"""Database access layer for the supply-chain blocklist policy.

Thin wrapper around ``PoolProtocol`` so the policy and background task stay
DB-agnostic: queries use asyncpg-style ``$N`` placeholders that the SQLite
translator understands. Nothing in this module imports asyncpg or aiosqlite
directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable, Sequence

from luthien_proxy.utils.db import PoolProtocol, parse_db_ts


@dataclass(frozen=True)
class BlocklistRow:
    """One row of the ``supply_chain_blocklist`` table."""

    ecosystem: str
    canonical_name: str
    cve_id: str
    affected_range: str
    severity: str
    published_at: datetime


async def load_all_entries(pool: PoolProtocol) -> list[BlocklistRow]:
    """Return every row in ``supply_chain_blocklist``, used at policy startup."""
    rows: Sequence[object] = await pool.fetch(
        """
        SELECT ecosystem, canonical_name, cve_id, affected_range, severity, published_at
        FROM supply_chain_blocklist
        """
    )
    return [_row_to_entry(r) for r in rows]


async def upsert_entries(pool: PoolProtocol, entries: Iterable[BlocklistRow]) -> int:
    """Insert or refresh rows from one background-task tick.

    Uses ``ON CONFLICT DO NOTHING`` semantics: the primary key is
    ``(ecosystem, canonical_name, cve_id, affected_range)``, so re-fetching
    the same advisory window is idempotent. Returns the number of attempted
    inserts (not a delta — the DB drivers don't report rows-affected uniformly).
    """
    count = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for entry in entries:
                await conn.execute(
                    """
                    INSERT INTO supply_chain_blocklist
                        (ecosystem, canonical_name, cve_id, affected_range, severity, published_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT DO NOTHING
                    """,
                    entry.ecosystem,
                    entry.canonical_name,
                    entry.cve_id,
                    entry.affected_range,
                    entry.severity,
                    entry.published_at.isoformat(),
                )
                count += 1
    return count


async def get_cursor(pool: PoolProtocol, ecosystem: str) -> datetime | None:
    """Return the most recent ``published_at`` watermark seen for ``ecosystem``."""
    row = await pool.fetchrow(
        "SELECT last_seen_at FROM supply_chain_blocklist_cursor WHERE ecosystem = $1",
        ecosystem,
    )
    if row is None:
        return None
    return parse_db_ts(row["last_seen_at"])


async def set_cursor(pool: PoolProtocol, ecosystem: str, last_seen_at: datetime) -> None:
    """Update the watermark, creating the row if missing."""
    now_iso = datetime.now(UTC).isoformat()
    await pool.execute(
        """
        INSERT INTO supply_chain_blocklist_cursor (ecosystem, last_seen_at, updated_at)
        VALUES ($1, $2, $3)
        ON CONFLICT (ecosystem) DO UPDATE SET last_seen_at = excluded.last_seen_at,
                                              updated_at = excluded.updated_at
        """,
        ecosystem,
        last_seen_at.isoformat(),
        now_iso,
    )


def _row_to_entry(row: object) -> BlocklistRow:
    record = row  # type: ignore[assignment]
    return BlocklistRow(
        ecosystem=str(record["ecosystem"]),  # type: ignore[index]
        canonical_name=str(record["canonical_name"]),  # type: ignore[index]
        cve_id=str(record["cve_id"]),  # type: ignore[index]
        affected_range=str(record["affected_range"]),  # type: ignore[index]
        severity=str(record["severity"]),  # type: ignore[index]
        published_at=parse_db_ts(record["published_at"]),  # type: ignore[index]
    )


__all__ = [
    "BlocklistRow",
    "get_cursor",
    "load_all_entries",
    "set_cursor",
    "upsert_entries",
]
