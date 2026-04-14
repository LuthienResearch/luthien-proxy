"""Unit tests for conversation-event full-text search (SQLite FTS5 + helper)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.db_sqlite import SqliteConnection
from luthien_proxy.utils.search import session_fts_filter_sql

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "migrations" / "sqlite"

REQUIRED_MIGRATIONS = (
    "003_add_conversation_tables.sql",
    "006_add_session_id.sql",
    "014_add_session_search_fts.sql",
)


async def _fetch_matches(pool: DatabasePool, query: str) -> list[dict]:
    async with pool.connection() as conn:
        rows = await conn.fetch(
            "SELECT event_id FROM conversation_events_fts WHERE conversation_events_fts MATCH $1",
            query,
        )
    return [dict(r) for r in rows]


async def _apply_migration(pool: DatabasePool, filename: str) -> None:
    async with pool.connection() as conn:
        assert isinstance(conn, SqliteConnection)
        await conn.executescript((MIGRATIONS_DIR / filename).read_text())


async def _fresh_fts_pool() -> DatabasePool:
    pool = DatabasePool("sqlite://:memory:")
    for name in REQUIRED_MIGRATIONS:
        await _apply_migration(pool, name)
    return pool


async def _insert_event(
    pool: DatabasePool,
    *,
    event_id: str,
    session_id: str,
    payload: dict,
    event_type: str = "transaction.request_recorded",
    call_id: str = "call-1",
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO conversation_calls (call_id) VALUES ($1)",
            call_id,
        )
        await conn.execute(
            "INSERT INTO conversation_events "
            "(id, call_id, event_type, sequence, payload, session_id) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            event_id,
            call_id,
            event_type,
            0,
            json.dumps(payload),
            session_id,
        )


@pytest.mark.asyncio
async def test_fts_indexes_user_content() -> None:
    """User-message text should be MATCHable in the FTS table."""
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-user",
            session_id="s1",
            payload={"final_request": {"messages": [{"role": "user", "content": "raspberry pancakes"}]}},
        )
        rows = await _fetch_matches(
            pool,
            "raspberry",
        )
        assert [r["event_id"] for r in rows] == ["e-user"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fts_indexes_assistant_content() -> None:
    """Assistant-response text blocks should be MATCHable in the FTS table."""
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-assistant",
            session_id="s2",
            payload={
                "final_request": {"messages": [{"role": "user", "content": "hi"}]},
                "final_response": {"content": [{"type": "text", "text": "blueberry muffins"}]},
            },
        )
        rows = await _fetch_matches(
            pool,
            "blueberry",
        )
        assert [r["event_id"] for r in rows] == ["e-assistant"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fts_indexes_user_array_blocks() -> None:
    """User content given as an array of text blocks should be indexed."""
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-blocks",
            session_id="s3",
            payload={
                "final_request": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "chocolate"},
                                {"type": "image", "source": {"data": "base64stuff"}},
                                {"type": "text", "text": "croissants"},
                            ],
                        }
                    ]
                }
            },
        )
        rows = await _fetch_matches(
            pool,
            "chocolate croissants",
        )
        assert [r["event_id"] for r in rows] == ["e-blocks"]
    finally:
        await pool.close()


@pytest.mark.parametrize("structural_term", ["role", "final_request", "final_response", "content"])
@pytest.mark.asyncio
async def test_fts_does_not_match_json_structural_keys(structural_term: str) -> None:
    """Structural JSON keys must not leak into the FTS index."""
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-nomatch",
            session_id="s4",
            payload={"final_request": {"messages": [{"role": "user", "content": "benign text"}]}},
        )
        rows = await _fetch_matches(
            pool,
            structural_term,
        )
        assert rows == []
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fts_skips_non_request_recorded_events() -> None:
    """Only transaction.request_recorded events should enter the FTS table."""
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-skip",
            session_id="s5",
            event_type="stream.chunk",
            payload={"final_request": {"messages": [{"role": "user", "content": "shouldnotindex"}]}},
        )
        rows = await _fetch_matches(
            pool,
            "shouldnotindex",
        )
        assert rows == []
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fts_skips_assistant_only_messages_in_request() -> None:
    """Assistant-role messages in final_request.messages must not be indexed as user content."""
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-roles",
            session_id="s6",
            payload={
                "final_request": {
                    "messages": [
                        {"role": "assistant", "content": "secretword"},
                        {"role": "user", "content": "publicword"},
                    ]
                }
            },
        )
        hits_public = await _fetch_matches(pool, "publicword")
        hits_secret = await _fetch_matches(pool, "secretword")
        assert [r["event_id"] for r in hits_public] == ["e-roles"]
        assert hits_secret == []
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fts_backfill_covers_existing_rows() -> None:
    """Rows inserted before the FTS migration should be backfilled when migration runs."""
    pool = DatabasePool("sqlite://:memory:")
    try:
        for name in ("003_add_conversation_tables.sql", "006_add_session_id.sql"):
            await _apply_migration(pool, name)
        await _insert_event(
            pool,
            event_id="e-old",
            session_id="s7",
            payload={"final_request": {"messages": [{"role": "user", "content": "historic content"}]}},
        )
        await _apply_migration(pool, "014_add_session_search_fts.sql")

        rows = await _fetch_matches(
            pool,
            "historic",
        )
        assert [r["event_id"] for r in rows] == ["e-old"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fts_match_is_not_like_wildcard() -> None:
    """FTS5 MATCH parses its own query grammar — LIKE wildcards are not valid.

    Guards against the prior ``LIKE '%q%'`` substring-search design: a caller
    that passed ``%`` as the query against LIKE would match every row, but
    against FTS5 MATCH it is a syntax error, not a wildcard.
    """
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-no-wildcard",
            session_id="s8",
            payload={"final_request": {"messages": [{"role": "user", "content": "ordinary content"}]}},
        )
        with pytest.raises(Exception, match="fts5|syntax"):
            await _fetch_matches(pool, "%")
    finally:
        await pool.close()


def test_session_fts_filter_sql_sqlite() -> None:
    """SQLite dialect returns an FTS subquery predicate."""
    pool = DatabasePool("sqlite://:memory:")
    fragment = session_fts_filter_sql(pool, "$3")
    assert "conversation_events_fts" in fragment
    assert "MATCH $3" in fragment
    assert "search_vector" not in fragment


def test_session_fts_filter_sql_postgres() -> None:
    """Postgres dialect returns a tsvector/plainto_tsquery predicate."""
    pool = DatabasePool("postgresql://example/db")
    fragment = session_fts_filter_sql(pool, "$3")
    assert "search_vector" in fragment
    assert "plainto_tsquery" in fragment
    assert "$3" in fragment
    assert "conversation_events_fts" not in fragment
