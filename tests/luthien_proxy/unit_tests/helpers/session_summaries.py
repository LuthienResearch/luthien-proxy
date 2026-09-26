"""Rebuild ``session_summaries`` from seeded ``conversation_events`` rows.

A deployment maintains ``session_summaries`` incrementally inside the
event-write transaction (see ``observability.emitter``). Tests that seed
``conversation_events`` / ``conversation_calls`` directly bypass that path, so
the session list — which pages from ``session_summaries`` — would see nothing.

Rather than re-implement the maintenance arithmetic, this replays the backfill
statement that migration 021 itself runs over pre-existing events, so the
summary rows a test reads are built exactly as a migrated deployment's are.
"""

from __future__ import annotations

from pathlib import Path

from luthien_proxy.utils.db import ConnectionProtocol

_MIGRATION = Path(__file__).resolve().parents[4] / "migrations" / "sqlite" / "021_add_session_summaries.sql"
_BACKFILL_MARKER = "INSERT OR IGNORE INTO session_summaries"


def _backfill_statement() -> str:
    sql = _MIGRATION.read_text()
    start = sql.index(_BACKFILL_MARKER)
    end = sql.index(";", start)
    return sql[start:end]


_BACKFILL_SQL = _backfill_statement()


async def rebuild_session_summaries(conn: ConnectionProtocol) -> None:
    """Drop and rebuild every ``session_summaries`` row from ``conversation_events``."""
    await conn.execute("DELETE FROM session_summaries")
    await conn.execute(_BACKFILL_SQL)


__all__ = ["rebuild_session_summaries"]
