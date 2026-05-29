"""Tests for server-side session search filters on ``fetch_session_list``.

The end-to-end filter behavior (model / time range / full-text ``q`` /
policy_intervention) is exercised against a real in-memory SQLite database with
all migrations applied, so the FTS5 virtual table and its sync triggers are
live — these are integration-grade unit tests, not mock-driven.

The Postgres dialect has no test tier in this repo, so its clause shape is
covered by direct unit tests of ``_build_session_filter_sql`` with a fake
postgres pool (``test_build_session_filter_sql_*``). That catches FTS-fragment
and aggregate-syntax regressions on the PG path without a live database.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from luthien_proxy.history.models import SessionSearchParams
from luthien_proxy.history.service import _build_session_filter_sql, fetch_session_list
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.db_sqlite import SqliteConnection


@pytest.fixture
async def sqlite_pool() -> DatabasePool:
    """In-memory SQLite pool with all migrations applied (incl. FTS5 infra)."""
    pool = DatabasePool("sqlite://:memory:")
    migrations_dir = Path(__file__).parent.parent.parent.parent.parent / "migrations" / "sqlite"
    async with pool.connection() as conn:
        assert isinstance(conn, SqliteConnection)
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            await conn.executescript(migration_file.read_text())
    yield pool
    await pool.close()


async def _seed_request(
    pool: DatabasePool,
    *,
    call_id: str,
    session_id: str,
    created_at: str,
    model: str = "gpt-4",
    content: str = "hello world",
    user_id: str | None = None,
    event_id: str | None = None,
) -> None:
    """Insert one conversation_calls row + one request_recorded event.

    The FTS5 insert trigger fires on the event, indexing ``content`` from the
    payload's first user message.
    """
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO conversation_calls (call_id, model_name, provider, status, session_id, user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            call_id,
            model,
            "openai",
            "completed",
            session_id,
            user_id,
            created_at,
        )
        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            event_id or f"event-{call_id}",
            call_id,
            "transaction.request_recorded",
            json.dumps(
                {
                    "final_model": model,
                    "final_request": {"messages": [{"role": "user", "content": content}]},
                }
            ),
            session_id,
            created_at,
        )


async def _add_event(
    pool: DatabasePool,
    *,
    event_id: str,
    call_id: str,
    session_id: str,
    event_type: str,
    created_at: str,
    payload: dict | None = None,
) -> None:
    """Append an extra event (e.g. a policy intervention) to an existing session."""
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            event_id,
            call_id,
            event_type,
            json.dumps(payload or {}),
            session_id,
            created_at,
        )


class TestModelFilter:
    @pytest.mark.asyncio
    async def test_model_filter_returns_only_matching(self, sqlite_pool: DatabasePool):
        await _seed_request(
            sqlite_pool, call_id="c1", session_id="s-opus", created_at="2026-04-01T10:00:00", model="claude-opus-4-6"
        )
        await _seed_request(
            sqlite_pool, call_id="c2", session_id="s-gpt", created_at="2026-04-01T11:00:00", model="gpt-4"
        )

        result = await fetch_session_list(
            limit=10, db_pool=sqlite_pool, search=SessionSearchParams(model="claude-opus-4-6")
        )
        assert [s.session_id for s in result.sessions] == ["s-opus"]
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_model_filter_no_match(self, sqlite_pool: DatabasePool):
        await _seed_request(sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00", model="gpt-4")
        result = await fetch_session_list(
            limit=10, db_pool=sqlite_pool, search=SessionSearchParams(model="model-that-does-not-exist")
        )
        assert result.sessions == []
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_model_filter_keeps_full_session_stats(self, sqlite_pool: DatabasePool):
        """A session qualifies on one model but its stats include all turns."""
        await _seed_request(sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00", model="gpt-4")
        await _seed_request(
            sqlite_pool, call_id="c2", session_id="s1", created_at="2026-04-01T10:05:00", model="claude-opus-4-6"
        )
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=SessionSearchParams(model="gpt-4"))
        assert len(result.sessions) == 1
        session = result.sessions[0]
        # Both turns counted even though only one used gpt-4.
        assert session.turn_count == 2
        assert sorted(session.models_used) == ["claude-opus-4-6", "gpt-4"]


class TestContentSearch:
    @pytest.mark.asyncio
    async def test_q_matches_user_message_text(self, sqlite_pool: DatabasePool):
        await _seed_request(
            sqlite_pool,
            call_id="c1",
            session_id="s-needle",
            created_at="2026-04-01T10:00:00",
            content="please find the needle",
        )
        await _seed_request(
            sqlite_pool,
            call_id="c2",
            session_id="s-other",
            created_at="2026-04-01T11:00:00",
            content="totally unrelated text",
        )
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=SessionSearchParams(q="needle"))
        assert [s.session_id for s in result.sessions] == ["s-needle"]

    @pytest.mark.asyncio
    async def test_q_is_porter_stemmed(self, sqlite_pool: DatabasePool):
        """FTS5 porter tokenizer stems, so 'run' matches 'running' (parity with PG english config)."""
        await _seed_request(
            sqlite_pool,
            call_id="c1",
            session_id="s1",
            created_at="2026-04-01T10:00:00",
            content="the server is running",
        )
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=SessionSearchParams(q="run"))
        assert [s.session_id for s in result.sessions] == ["s1"]

    @pytest.mark.asyncio
    async def test_q_no_match_returns_empty(self, sqlite_pool: DatabasePool):
        await _seed_request(
            sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00", content="hello"
        )
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=SessionSearchParams(q="absent"))
        assert result.sessions == []
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_q_with_fts_special_chars_does_not_crash(self, sqlite_pool: DatabasePool):
        """FTS5 metacharacters in q are sanitized by session_fts_filter_sql, not injected."""
        await _seed_request(
            sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00", content="hello"
        )
        # A query full of FTS5 operators must not raise; it simply matches nothing here.
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=SessionSearchParams(q='" OR - + : ('))
        assert result.sessions == []


class TestTimeRangeFilter:
    @pytest.mark.asyncio
    async def test_to_time_excludes_sessions_with_later_activity(self, sqlite_pool: DatabasePool):
        """Last-activity semantics: a session whose last event is after to_time is excluded."""
        # Session spans 10:00 and 12:00; to_time is 11:00 → last activity (12:00) > to → excluded.
        await _seed_request(sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00")
        await _add_event(
            sqlite_pool,
            event_id="e-late",
            call_id="c1",
            session_id="s1",
            event_type="transaction.streaming_response_recorded",
            created_at="2026-04-01T12:00:00",
        )
        result = await fetch_session_list(
            limit=10, db_pool=sqlite_pool, search=SessionSearchParams(to_time=datetime(2026, 4, 1, 11, 0, 0))
        )
        assert result.sessions == []

    @pytest.mark.asyncio
    async def test_time_range_keeps_full_stats_for_contained_session(self, sqlite_pool: DatabasePool):
        """Regression (#581 review): time filter is a HAVING on last-activity, so it must
        not mutilate per-session stats by dropping out-of-window events."""
        await _seed_request(sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00")
        await _add_event(
            sqlite_pool,
            event_id="e2",
            call_id="c1",
            session_id="s1",
            event_type="transaction.streaming_response_recorded",
            created_at="2026-04-01T10:00:01",
        )
        result = await fetch_session_list(
            limit=10,
            db_pool=sqlite_pool,
            search=SessionSearchParams(from_time=datetime(2026, 4, 1, 9, 0, 0), to_time=datetime(2026, 4, 1, 11, 0, 0)),
        )
        assert len(result.sessions) == 1
        # Both events counted — the time filter did not strip the response event.
        assert result.sessions[0].total_events == 2

    @pytest.mark.asyncio
    async def test_from_time_excludes_older_sessions(self, sqlite_pool: DatabasePool):
        await _seed_request(sqlite_pool, call_id="c-old", session_id="s-old", created_at="2026-03-01T10:00:00")
        await _seed_request(sqlite_pool, call_id="c-new", session_id="s-new", created_at="2026-04-15T10:00:00")
        result = await fetch_session_list(
            limit=10, db_pool=sqlite_pool, search=SessionSearchParams(from_time=datetime(2026, 4, 1, 0, 0, 0))
        )
        assert [s.session_id for s in result.sessions] == ["s-new"]


class TestPolicyInterventionFilter:
    @pytest.mark.asyncio
    async def test_only_sessions_with_interventions(self, sqlite_pool: DatabasePool):
        await _seed_request(sqlite_pool, call_id="c-clean", session_id="s-clean", created_at="2026-04-01T10:00:00")
        await _seed_request(sqlite_pool, call_id="c-flagged", session_id="s-flagged", created_at="2026-04-01T11:00:00")
        await _add_event(
            sqlite_pool,
            event_id="e-block",
            call_id="c-flagged",
            session_id="s-flagged",
            event_type="policy.anthropic_judge.tool_call_blocked",
            created_at="2026-04-01T11:00:01",
            payload={"summary": "blocked"},
        )
        result = await fetch_session_list(
            limit=10, db_pool=sqlite_pool, search=SessionSearchParams(policy_intervention=True)
        )
        assert [s.session_id for s in result.sessions] == ["s-flagged"]
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_evaluation_events_do_not_qualify(self, sqlite_pool: DatabasePool):
        """A session whose only policy events are judge evaluations is not an intervention."""
        await _seed_request(sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00")
        await _add_event(
            sqlite_pool,
            event_id="e-eval",
            call_id="c1",
            session_id="s1",
            event_type="policy.anthropic_judge.evaluation_started",
            created_at="2026-04-01T10:00:01",
        )
        result = await fetch_session_list(
            limit=10, db_pool=sqlite_pool, search=SessionSearchParams(policy_intervention=True)
        )
        assert result.sessions == []


class TestCombinedFilters:
    @pytest.mark.asyncio
    async def test_model_and_q_and_time_combined(self, sqlite_pool: DatabasePool):
        # Target: opus session containing "needle" in April.
        await _seed_request(
            sqlite_pool,
            call_id="c-hit",
            session_id="s-hit",
            created_at="2026-04-10T10:00:00",
            model="claude-opus-4-6",
            content="the needle is here",
        )
        # Right model + text but wrong time.
        await _seed_request(
            sqlite_pool,
            call_id="c-oldtime",
            session_id="s-oldtime",
            created_at="2026-01-10T10:00:00",
            model="claude-opus-4-6",
            content="the needle is here",
        )
        # Right time + text but wrong model.
        await _seed_request(
            sqlite_pool,
            call_id="c-wrongmodel",
            session_id="s-wrongmodel",
            created_at="2026-04-10T10:00:00",
            model="gpt-4",
            content="the needle is here",
        )
        # Right model + time but wrong text.
        await _seed_request(
            sqlite_pool,
            call_id="c-wrongtext",
            session_id="s-wrongtext",
            created_at="2026-04-10T10:00:00",
            model="claude-opus-4-6",
            content="nothing relevant",
        )
        result = await fetch_session_list(
            limit=10,
            db_pool=sqlite_pool,
            search=SessionSearchParams(
                model="claude-opus-4-6",
                q="needle",
                from_time=datetime(2026, 4, 1, 0, 0, 0),
                to_time=datetime(2026, 4, 30, 0, 0, 0),
            ),
        )
        assert [s.session_id for s in result.sessions] == ["s-hit"]
        assert result.total == 1


class TestTotalReflectsFilter:
    @pytest.mark.asyncio
    async def test_total_is_filtered_count_not_global(self, sqlite_pool: DatabasePool):
        await _seed_request(sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00", model="gpt-4")
        await _seed_request(sqlite_pool, call_id="c2", session_id="s2", created_at="2026-04-01T11:00:00", model="gpt-4")
        await _seed_request(
            sqlite_pool, call_id="c3", session_id="s3", created_at="2026-04-01T12:00:00", model="claude-opus-4-6"
        )
        result = await fetch_session_list(
            limit=10, db_pool=sqlite_pool, search=SessionSearchParams(model="claude-opus-4-6")
        )
        assert result.total == 1  # filtered, not the global 3
        assert result.has_more is False


class TestEmptySearchParityWithUnfiltered:
    @pytest.mark.asyncio
    async def test_empty_search_matches_no_search(self, sqlite_pool: DatabasePool):
        await _seed_request(sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00")
        await _seed_request(sqlite_pool, call_id="c2", session_id="s2", created_at="2026-04-01T11:00:00")
        unfiltered = await fetch_session_list(limit=10, db_pool=sqlite_pool)
        empty_search = await fetch_session_list(limit=10, db_pool=sqlite_pool, search=SessionSearchParams())
        assert unfiltered.total == empty_search.total == 2
        assert [s.session_id for s in unfiltered.sessions] == [s.session_id for s in empty_search.sessions]


class TestSearchInjectionSafe:
    @pytest.mark.asyncio
    async def test_model_value_is_bound_not_interpolated(self, sqlite_pool: DatabasePool):
        await _seed_request(sqlite_pool, call_id="c1", session_id="s1", created_at="2026-04-01T10:00:00", model="gpt-4")
        result = await fetch_session_list(
            limit=10,
            db_pool=sqlite_pool,
            search=SessionSearchParams(model="gpt-4'; DROP TABLE conversation_events;--"),
        )
        assert result.sessions == []
        # Table still intact and queryable.
        intact = await fetch_session_list(limit=10, db_pool=sqlite_pool)
        assert len(intact.sessions) == 1


class _FakePool:
    """Minimal DatabasePool stand-in for dialect-shape unit tests."""

    def __init__(self, *, is_postgres: bool) -> None:
        self.is_postgres = is_postgres
        self.is_sqlite = not is_postgres


class TestBuildSessionFilterSql:
    """Dialect-shape coverage for the clause builder (esp. the un-runnable PG path)."""

    def test_empty_search_produces_no_clauses(self):
        args: list = [1, 2]
        gates, having = _build_session_filter_sql(SessionSearchParams(), _FakePool(is_postgres=True), args)
        assert gates == []
        assert having == []
        assert args == [1, 2]  # untouched

    def test_postgres_q_uses_plainto_tsquery_and_search_vector(self):
        args: list = []
        gates, having = _build_session_filter_sql(SessionSearchParams(q="needle"), _FakePool(is_postgres=True), args)
        assert len(gates) == 1
        assert "search_vector @@ plainto_tsquery('english', $1)" in gates[0]
        assert args == ["needle"]  # raw value bound; PG sanitizes via plainto_tsquery

    def test_sqlite_q_uses_fts_match_table(self):
        args: list = []
        gates, _ = _build_session_filter_sql(SessionSearchParams(q="needle"), _FakePool(is_postgres=False), args)
        assert "conversation_events_fts MATCH $1" in gates[0]
        assert args == ['"needle"']  # phrase-quoted for FTS5

    def test_postgres_model_uses_jsonb_arrow(self):
        args: list = []
        gates, _ = _build_session_filter_sql(
            SessionSearchParams(model="claude-opus-4-6"), _FakePool(is_postgres=True), args
        )
        assert "ce.payload->>'final_model' = $1" in gates[0]
        assert args == ["claude-opus-4-6"]

    def test_sqlite_model_uses_json_extract(self):
        args: list = []
        gates, _ = _build_session_filter_sql(SessionSearchParams(model="gpt-4"), _FakePool(is_postgres=False), args)
        assert "json_extract(ce.payload, '$.final_model') = $1" in gates[0]

    def test_postgres_policy_intervention_uses_filter_aggregate(self):
        args: list = []
        _, having = _build_session_filter_sql(
            SessionSearchParams(policy_intervention=True), _FakePool(is_postgres=True), args
        )
        assert any("FILTER (WHERE ce.event_type LIKE 'policy.%'" in h and "> 0" in h for h in having)

    def test_sqlite_policy_intervention_uses_sum_case(self):
        args: list = []
        _, having = _build_session_filter_sql(
            SessionSearchParams(policy_intervention=True), _FakePool(is_postgres=False), args
        )
        assert any("SUM(CASE WHEN ce.event_type LIKE 'policy.%'" in h for h in having)

    def test_time_bounds_use_max_having_and_dialect_bind(self):
        # Postgres binds the datetime object; SQLite binds an ISO string.
        dt = datetime(2026, 4, 1, 12, 0, 0)
        pg_args: list = []
        _, pg_having = _build_session_filter_sql(
            SessionSearchParams(from_time=dt, to_time=dt), _FakePool(is_postgres=True), pg_args
        )
        assert pg_having == ["MAX(ce.created_at) >= $1", "MAX(ce.created_at) <= $2"]
        assert pg_args == [dt, dt]

        sqlite_args: list = []
        _build_session_filter_sql(
            SessionSearchParams(from_time=dt, to_time=dt), _FakePool(is_postgres=False), sqlite_args
        )
        assert sqlite_args == [dt.isoformat(), dt.isoformat()]

    def test_user_scope_sql_appended_to_gate_subqueries(self):
        args: list = []
        scope = "AND ce.call_id IN (SELECT call_id FROM conversation_calls WHERE user_id = $3)"
        gates, _ = _build_session_filter_sql(
            SessionSearchParams(model="gpt-4"), _FakePool(is_postgres=True), args, user_scope_sql=scope
        )
        assert scope in gates[0]


__all__ = []
