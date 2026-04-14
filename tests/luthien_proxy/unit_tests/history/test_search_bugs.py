"""Tests demonstrating bugs in the session search implementation.

These tests hit real SQLite to exercise the actual SQL query construction,
unlike test_search.py which mocks the DB and can't catch SQL-level bugs.

Bug 1: from_time/to_time filter applied to raw event rows, not aggregated session timestamps.
        A session spanning Jan-Mar queried with from_time=Feb returns mutilated stats.

Bug 2: SQLite q parameter missing LIKE wildcard escaping.
        q=% or q=_ matches everything; diverges from Postgres plainto_tsquery behavior.

Bug 3: SQLite q search matches raw JSON structure, not just content.
        q=role or q=type matches every event because of JSON keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from luthien_proxy.history.models import SessionSearchParams
from luthien_proxy.history.service import fetch_session_list
from luthien_proxy.utils.db import DatabasePool


@pytest.fixture
async def sqlite_pool() -> DatabasePool:
    """Create an in-memory SQLite pool with schema applied."""
    pool = DatabasePool("sqlite://:memory:")

    migrations_dir = Path(__file__).parent.parent.parent.parent.parent / "migrations" / "sqlite"

    async with pool.connection() as conn:
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            sql = migration_file.read_text()
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement and not all(
                    line.strip().startswith("--") or not line.strip() for line in statement.split("\n")
                ):
                    await conn.execute(statement)

    yield pool
    await pool.close()


async def _insert_event(
    pool: DatabasePool,
    *,
    event_id: str,
    call_id: str,
    session_id: str,
    event_type: str = "transaction.request_recorded",
    payload: dict | None = None,
    created_at: str,
) -> None:
    """Insert a conversation event (and its call if needed)."""
    if payload is None:
        payload = {
            "final_model": "claude-opus-4-6",
            "final_request": {"messages": [{"role": "user", "content": "Hello"}]},
        }

    async with pool.connection() as conn:
        # Upsert the call
        await conn.execute(
            """
            INSERT OR IGNORE INTO conversation_calls
            (call_id, model_name, provider, status, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            call_id,
            "claude-opus-4-6",
            "anthropic",
            "completed",
            session_id,
            created_at,
        )

        await conn.execute(
            """
            INSERT INTO conversation_events
            (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            event_id,
            call_id,
            event_type,
            json.dumps(payload),
            session_id,
            created_at,
        )


class TestBug1_TimeFilterMutilatesSessionStats:
    """Bug: from_time/to_time is applied to individual event rows, not session-level aggregates.

    The docstring says "lower bound on session last activity" but the WHERE clause
    filters raw events, so a session spanning Jan-Mar queried with from_time=Feb
    returns wrong first_ts, total_events, and turn_count (computed only from Feb+ events).
    """

    @pytest.mark.asyncio
    async def test_from_time_should_not_mutilate_session_stats(self, sqlite_pool: DatabasePool):
        """A session with events in Jan and Mar, filtered with from_time=Feb,
        should return the session with its REAL stats (first_ts=Jan, total_events=2),
        not stats computed only from the Mar event."""

        # Session A: events in January and March
        await _insert_event(
            sqlite_pool,
            event_id="e1",
            call_id="c1",
            session_id="session-A",
            created_at="2026-01-15T10:00:00",
            payload={
                "final_model": "claude-opus-4-6",
                "final_request": {"messages": [{"role": "user", "content": "January message"}]},
            },
        )
        await _insert_event(
            sqlite_pool,
            event_id="e2",
            call_id="c2",
            session_id="session-A",
            created_at="2026-03-15T10:00:00",
            payload={
                "final_model": "claude-opus-4-6",
                "final_request": {"messages": [{"role": "user", "content": "March message"}]},
            },
        )

        # Unfiltered: should show 2 events, first_ts in January
        unfiltered = await fetch_session_list(limit=10, db_pool=sqlite_pool)
        assert len(unfiltered.sessions) == 1
        session = unfiltered.sessions[0]
        assert session.total_events == 2
        assert "2026-01-15" in session.first_timestamp

        # Filtered: from_time=Feb should still return the session (last activity is March)
        # and should show the REAL stats, not just the March-onwards slice
        search = SessionSearchParams(
            from_time=__import__("datetime").datetime(2026, 2, 1, tzinfo=__import__("datetime").timezone.utc)
        )
        filtered = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=search)

        # The session should appear (its last activity is March, after Feb)
        assert len(filtered.sessions) == 1, "Session with last_ts=March should match from_time=Feb"

        filtered_session = filtered.sessions[0]

        # BUG: These assertions demonstrate the bug.
        # The filter should not change the session's intrinsic stats.
        # But the current implementation computes stats only from events >= Feb,
        # so first_ts becomes March and total_events becomes 1.
        assert filtered_session.total_events == 2, (
            f"BUG: total_events={filtered_session.total_events}, expected 2. "
            "The time filter is mutilating session stats by excluding early events from the aggregate."
        )
        assert "2026-01-15" in filtered_session.first_timestamp, (
            f"BUG: first_timestamp={filtered_session.first_timestamp}, expected 2026-01-15. "
            "The time filter shifted first_ts to the filter boundary."
        )

    @pytest.mark.asyncio
    async def test_to_time_should_not_exclude_later_events_from_stats(self, sqlite_pool: DatabasePool):
        """A session with events in Jan and Mar, filtered with to_time=Feb,
        should return the session (if its first activity is before Feb)
        with its REAL stats, not stats computed only from the Jan event."""

        await _insert_event(
            sqlite_pool,
            event_id="e1",
            call_id="c1",
            session_id="session-A",
            created_at="2026-01-15T10:00:00",
        )
        await _insert_event(
            sqlite_pool,
            event_id="e2",
            call_id="c2",
            session_id="session-A",
            created_at="2026-03-15T10:00:00",
        )

        search = SessionSearchParams(
            to_time=__import__("datetime").datetime(2026, 2, 1, tzinfo=__import__("datetime").timezone.utc)
        )
        filtered = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=search)

        if len(filtered.sessions) == 1:
            # If the session is returned, its stats should be the real ones
            assert filtered.sessions[0].total_events == 2, (
                f"BUG: total_events={filtered.sessions[0].total_events}, expected 2. "
                "The to_time filter is excluding later events from the aggregate."
            )


class TestBug2_SqliteLikeEscaping:
    """Bug: The q parameter is not escaped for LIKE metacharacters on SQLite.

    The user filter correctly escapes %, _, and \\ before constructing the LIKE pattern.
    The q filter does not, so q=% matches everything and q=_ matches everything
    with at least one character. This diverges from Postgres plainto_tsquery which
    treats input as literal text.
    """

    @pytest.mark.asyncio
    async def test_q_percent_should_not_match_everything(self, sqlite_pool: DatabasePool):
        """q=% should be treated as a literal percent sign search, not a wildcard."""

        await _insert_event(
            sqlite_pool,
            event_id="e1",
            call_id="c1",
            session_id="session-A",
            created_at="2026-01-15T10:00:00",
            payload={
                "final_model": "claude-opus-4-6",
                "final_request": {"messages": [{"role": "user", "content": "Hello world"}]},
            },
        )

        # Search for literal "%" — should NOT match "Hello world"
        search = SessionSearchParams(q="%")
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=search)

        assert len(result.sessions) == 0, (
            f"BUG: q='%' matched {len(result.sessions)} sessions. "
            "The percent sign is being treated as a LIKE wildcard instead of literal text. "
            "Expected 0 matches since no event contains a literal '%'."
        )

    @pytest.mark.asyncio
    async def test_q_underscore_should_not_match_everything(self, sqlite_pool: DatabasePool):
        """q=_ should be treated as a literal underscore search, not a single-char wildcard."""

        await _insert_event(
            sqlite_pool,
            event_id="e1",
            call_id="c1",
            session_id="session-A",
            created_at="2026-01-15T10:00:00",
            payload={
                "final_model": "claude-opus-4-6",
                "final_request": {"messages": [{"role": "user", "content": "Hello world"}]},
            },
        )

        # Search for literal "_" — should NOT match "Hello world"
        search = SessionSearchParams(q="_")
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=search)

        assert len(result.sessions) == 0, (
            f"BUG: q='_' matched {len(result.sessions)} sessions. "
            "The underscore is being treated as a LIKE single-char wildcard instead of literal text. "
            "Expected 0 matches since no event contains a literal '_'."
        )


class TestBug3_SqliteSearchesRawJson:
    """Bug: SQLite q search matches the entire serialized JSON payload, including
    structural keys like 'role', 'type', 'content', 'final_request', etc.

    Postgres uses _extract_event_search_text() which carefully extracts only user/assistant
    text content. SQLite uses `payload LIKE '%q%'` which matches JSON keys and metadata.
    """

    @pytest.mark.asyncio
    async def test_q_role_should_not_match_json_keys(self, sqlite_pool: DatabasePool):
        """Searching for 'role' should not match just because every event has
        a 'role' key in its JSON structure."""

        await _insert_event(
            sqlite_pool,
            event_id="e1",
            call_id="c1",
            session_id="session-A",
            created_at="2026-01-15T10:00:00",
            payload={
                "final_model": "claude-opus-4-6",
                "final_request": {"messages": [{"role": "user", "content": "What is the weather?"}]},
            },
        )

        # "role" appears in the JSON structure but not in the user's actual message
        search = SessionSearchParams(q="role")
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=search)

        assert len(result.sessions) == 0, (
            f"BUG: q='role' matched {len(result.sessions)} sessions. "
            "SQLite is matching against JSON structural keys, not just message content. "
            "Postgres uses _extract_event_search_text() to search only user/assistant text."
        )

    @pytest.mark.asyncio
    async def test_q_final_request_should_not_match_json_keys(self, sqlite_pool: DatabasePool):
        """Searching for 'final_request' should not match just because it's a JSON key."""

        await _insert_event(
            sqlite_pool,
            event_id="e1",
            call_id="c1",
            session_id="session-A",
            created_at="2026-01-15T10:00:00",
            payload={
                "final_model": "claude-opus-4-6",
                "final_request": {"messages": [{"role": "user", "content": "Tell me a joke"}]},
            },
        )

        search = SessionSearchParams(q="final_request")
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=search)

        assert len(result.sessions) == 0, (
            f"BUG: q='final_request' matched {len(result.sessions)} sessions. "
            "SQLite is matching against JSON structural keys, not just message content."
        )
