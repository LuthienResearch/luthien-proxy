"""Regression tests for session search bugs fixed during PR #578 review.

These tests hit real SQLite to exercise the actual SQL query construction,
unlike test_search.py which mocks the DB. Each class pins down a specific
semantic that was wrong in an earlier revision and must not regress.

- Time filters must not mutilate session stats (from_time/to_time were
  originally applied to raw event rows before aggregation).
- SQLite q must escape LIKE wildcards so q="%" / q="_" doesn't match everything.
- SQLite content search must target message text only, not raw JSON payload.

Assertion messages carry a "regression:" prefix so that if any of these come
back, the failure is self-describing.
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


class TestTimeFilterDoesNotMutilateStats:
    """Time filters must match on aggregated session timestamps (MAX/MIN of
    created_at) and must not truncate first_ts/total_events/turn_count by
    excluding early or late events from the aggregate.

    Semantics (matching docstrings in models.py):
      - from_time: lower bound on session last activity (MAX >= from_time)
      - to_time: upper bound on session last activity (MAX <= to_time)
    """

    @pytest.mark.asyncio
    async def test_from_time_returns_real_stats_for_session_spanning_boundary(self, sqlite_pool: DatabasePool):
        """from_time=Feb on a Jan+Mar session returns the session with
        total_events=2, first_ts=Jan — not stats recomputed from Feb+ events only."""

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

        unfiltered = await fetch_session_list(limit=10, db_pool=sqlite_pool)
        assert len(unfiltered.sessions) == 1
        session = unfiltered.sessions[0]
        assert session.total_events == 2
        assert "2026-01-15" in session.first_timestamp

        search = SessionSearchParams(
            from_time=__import__("datetime").datetime(2026, 2, 1, tzinfo=__import__("datetime").timezone.utc)
        )
        filtered = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=search)

        assert len(filtered.sessions) == 1, "Session with last_ts=March should match from_time=Feb"

        filtered_session = filtered.sessions[0]

        assert filtered_session.total_events == 2, (
            f"regression: total_events={filtered_session.total_events}, expected 2. "
            "Time filter is excluding early events from the aggregate."
        )
        assert "2026-01-15" in filtered_session.first_timestamp, (
            f"regression: first_timestamp={filtered_session.first_timestamp}, expected 2026-01-15. "
            "Time filter shifted first_ts to the filter boundary."
        )

    @pytest.mark.asyncio
    async def test_to_time_excludes_sessions_whose_last_activity_is_after_bound(self, sqlite_pool: DatabasePool):
        """to_time=Feb on a Jan+Mar session excludes the session entirely —
        its last activity (Mar) is after the upper bound."""

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

        assert len(filtered.sessions) == 0, (
            "regression: session with last_ts=March was included for to_time=Feb. "
            "to_time should be an upper bound on session last activity (MAX <= to_time)."
        )

    @pytest.mark.asyncio
    async def test_to_time_returns_real_stats_for_fully_contained_session(self, sqlite_pool: DatabasePool):
        """to_time=Feb on a session with events entirely in January returns
        the session with full stats from all its events."""

        await _insert_event(
            sqlite_pool,
            event_id="e1",
            call_id="c1",
            session_id="session-A",
            created_at="2026-01-10T10:00:00",
        )
        await _insert_event(
            sqlite_pool,
            event_id="e2",
            call_id="c2",
            session_id="session-A",
            created_at="2026-01-20T10:00:00",
        )

        search = SessionSearchParams(
            to_time=__import__("datetime").datetime(2026, 2, 1, tzinfo=__import__("datetime").timezone.utc)
        )
        filtered = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=search)

        assert len(filtered.sessions) == 1
        assert filtered.sessions[0].total_events == 2


class TestSqliteLikeEscaping:
    """The q parameter must escape LIKE metacharacters on SQLite so that
    searches for literal %, _, or \\ behave as plain text — not wildcards.
    Matches Postgres plainto_tsquery semantics for the same input."""

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
            f"regression:q='%' matched {len(result.sessions)} sessions. "
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
            f"regression:q='_' matched {len(result.sessions)} sessions. "
            "The underscore is being treated as a LIKE single-char wildcard instead of literal text. "
            "Expected 0 matches since no event contains a literal '_'."
        )


class TestSqliteSearchesContentNotJsonKeys:
    """SQLite q search must target message text values only, not raw JSON
    structural keys ('role', 'type', 'final_request', etc.) that appear in
    every event payload."""

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
            f"regression:q='role' matched {len(result.sessions)} sessions. "
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
            f"regression:q='final_request' matched {len(result.sessions)} sessions. "
            "SQLite is matching against JSON structural keys, not just message content."
        )
