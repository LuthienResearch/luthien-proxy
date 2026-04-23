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
        rows = await _fetch_matches(pool, "raspberry")
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
        rows = await _fetch_matches(pool, "blueberry")
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
        rows = await _fetch_matches(pool, "chocolate croissants")
        assert [r["event_id"] for r in rows] == ["e-blocks"]
    finally:
        await pool.close()


@pytest.mark.parametrize("structural_term", ["role", "final_request", "final_response"])
@pytest.mark.asyncio
async def test_fts_does_not_match_json_structural_keys(structural_term: str) -> None:
    """Structural JSON keys must not leak into the FTS index.

    (``content`` is omitted because porter-stemmed tokenization leaves real
    English words like ``content`` intact, and user payloads can legitimately
    contain the literal word.)
    """
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-nomatch",
            session_id="s4",
            payload={"final_request": {"messages": [{"role": "user", "content": "benign text"}]}},
        )
        rows = await _fetch_matches(pool, structural_term)
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
        rows = await _fetch_matches(pool, "shouldnotindex")
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

        rows = await _fetch_matches(pool, "historic")
        assert [r["event_id"] for r in rows] == ["e-old"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fts_stems_english_terms() -> None:
    """Porter tokenizer should match inflected forms of the same stem.

    This gives dialect parity with Postgres ``plainto_tsquery('english', ...)``.
    """
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-stem",
            session_id="s-stem",
            payload={"final_request": {"messages": [{"role": "user", "content": "running quickly"}]}},
        )
        # Query with different inflection of the indexed token.
        rows = await _fetch_matches(pool, "run")
        assert [r["event_id"] for r in rows] == ["e-stem"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fts_delete_trigger_removes_row() -> None:
    """Deleting a conversation_events row must drop its FTS entry.

    Without this, MATCH would keep returning stale event_ids pointing to rows
    that no longer exist in conversation_events.
    """
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-del",
            session_id="s-del",
            payload={"final_request": {"messages": [{"role": "user", "content": "ephemeral"}]}},
        )
        assert await _fetch_matches(pool, "ephemeral")

        async with pool.connection() as conn:
            await conn.execute("DELETE FROM conversation_events WHERE id = $1", "e-del")

        assert await _fetch_matches(pool, "ephemeral") == []
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fts_cascade_delete_from_parent_call() -> None:
    """Dropping a conversation_calls row (CASCADE) must remove child FTS entries.

    Guards against orphan FTS rows when callers tidy history by deleting a
    call; the CASCADE removes the event, and the DELETE trigger on events
    removes its FTS row.
    """
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-cascade",
            session_id="s-cascade",
            call_id="call-cascade",
            payload={"final_request": {"messages": [{"role": "user", "content": "cascadetoken"}]}},
        )
        assert await _fetch_matches(pool, "cascadetoken")

        async with pool.connection() as conn:
            # Foreign keys are enforced globally via PRAGMA foreign_keys=ON
            # in SqlitePool._get_conn; this is what makes CASCADE fire.
            await conn.execute("DELETE FROM conversation_calls WHERE call_id = $1", "call-cascade")

        assert await _fetch_matches(pool, "cascadetoken") == []
    finally:
        await pool.close()


@pytest.mark.parametrize(
    "dangerous_query",
    ["%", "'", "foo-bar", "foo+bar", 'foo "bar', "content:nope", '"', "-baz"],
)
@pytest.mark.asyncio
async def test_helper_sanitizes_fts5_special_characters(dangerous_query: str) -> None:
    """Queries containing FTS5 meta-characters must not crash MATCH.

    FTS5 parses its own query grammar and throws on raw ``'``, ``-``, ``+``,
    column-filter prefixes like ``name:``, stray ``"``, etc. The helper takes
    ownership of these inputs and returns a safe quoted-phrase expression.
    """
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-safe",
            session_id="s-safe",
            payload={"final_request": {"messages": [{"role": "user", "content": "ordinary words here"}]}},
        )
        fragment, bind_value = session_fts_filter_sql(pool, dangerous_query, placeholder="$1")
        assert "conversation_events_fts" in fragment
        # Must not raise -- MATCH parses the sanitized phrase.
        async with pool.connection() as conn:
            await conn.fetch(
                "SELECT event_id FROM conversation_events_fts WHERE conversation_events_fts MATCH $1",
                bind_value,
            )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_helper_returns_bind_value_matching_content() -> None:
    """End-to-end: helper output, bound via asyncpg-style params, hits indexed rows."""
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-bind",
            session_id="s-bind",
            payload={"final_request": {"messages": [{"role": "user", "content": "salmon risotto"}]}},
        )
        fragment, bind_value = session_fts_filter_sql(pool, "salmon", placeholder="$1")
        # Substitute the fragment into a realistic parent query.
        async with pool.connection() as conn:
            rows = await conn.fetch(
                f"SELECT ce.id AS event_id FROM conversation_events ce WHERE {fragment}",
                bind_value,
            )
        assert [r["event_id"] for r in rows] == ["e-bind"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_helper_empty_query_matches_nothing() -> None:
    """Empty/whitespace input yields zero matches, mirroring plainto_tsquery('')."""
    pool = await _fresh_fts_pool()
    try:
        await _insert_event(
            pool,
            event_id="e-empty",
            session_id="s-empty",
            payload={"final_request": {"messages": [{"role": "user", "content": "anything"}]}},
        )
        fragment, bind_value = session_fts_filter_sql(pool, "   ", placeholder="$1")
        async with pool.connection() as conn:
            rows = await conn.fetch(
                f"SELECT ce.id AS event_id FROM conversation_events ce WHERE {fragment}",
                bind_value,
            )
        assert rows == []
    finally:
        await pool.close()


def test_session_fts_filter_sql_sqlite_shape() -> None:
    """SQLite dialect returns the FTS subquery predicate and sanitized bind value."""
    pool = DatabasePool("sqlite://:memory:")
    fragment, bind_value = session_fts_filter_sql(pool, "chocolate croissants", placeholder="$3")
    assert "conversation_events_fts" in fragment
    assert "MATCH $3" in fragment
    assert "search_vector" not in fragment
    assert bind_value == '"chocolate" "croissants"'


def test_session_fts_filter_sql_postgres_shape() -> None:
    """Postgres dialect returns the tsvector predicate and passes the query through."""
    pool = DatabasePool("postgresql://example/db")
    fragment, bind_value = session_fts_filter_sql(pool, "chocolate croissants", placeholder="$3")
    assert "search_vector" in fragment
    assert "plainto_tsquery" in fragment
    assert "$3" in fragment
    assert "conversation_events_fts" not in fragment
    # Postgres side hands the query to plainto_tsquery unchanged.
    assert bind_value == "chocolate croissants"


def test_session_fts_filter_sql_sqlite_escapes_quotes() -> None:
    """Embedded double-quotes are doubled inside the FTS5 phrase."""
    pool = DatabasePool("sqlite://:memory:")
    _, bind_value = session_fts_filter_sql(pool, 'foo"bar', placeholder="$1")
    assert bind_value == '"foo""bar"'
