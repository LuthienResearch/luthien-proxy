"""Postgres-backed checks for the filtered / user-scoped session list.

The session list pages from ``session_summaries`` and reads
``conversation_events`` only for the sessions on the page. These tests prove
that against a live Postgres: they run the exact SQL the service issued under
``EXPLAIN (ANALYZE, FORMAT JSON)`` and count the ``conversation_events`` rows
the plan actually touched. Before the fix every page load aggregated the whole
table three times, regardless of page size (AGENTC-168).

Needs a reachable Postgres (``DATABASE_URL`` or ``PG*`` env, same as
``test_migration_sync.py``); a throwaway database is created and dropped.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import asyncpg
import pytest

from luthien_proxy.history.models import SessionSearchParams
from luthien_proxy.history.service import fetch_session_list
from luthien_proxy.observability.session_summary import update_session_summary
from luthien_proxy.utils.db import ConnectionProtocol, DatabasePool

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "postgres"

ALICE = "alice"
BOB = "bob"
OPUS = "claude-opus-4-6"
GPT = "gpt-4"

# Alice's sessions inside the searched window, newest first.
APRIL_SESSIONS = ["alice-april-3", "alice-april-2", "alice-april-1"]
EVENTS_PER_APRIL_SESSION = 4
# Bulk that a page load must never read: bob's sessions on another model, and
# alice's sessions outside the window.
BOB_SESSIONS = 200
BOB_EVENTS_PER_SESSION = 25
ALICE_MARCH_SESSIONS = 10
ALICE_MARCH_EVENTS_PER_SESSION = 10


def _admin_dsn() -> str:
    return os.environ.get("DATABASE_URL") or (
        f"postgresql://{os.environ.get('PGUSER', 'luthien')}"
        f":{os.environ.get('PGPASSWORD', 'luthien')}"
        f"@{os.environ.get('PGHOST', 'localhost')}"
        f":{os.environ.get('PGPORT', '5432')}"
        f"/{os.environ.get('PGDATABASE', 'luthien_control')}"
    )


@pytest.fixture
async def pg_pool() -> AsyncIterator[DatabasePool]:
    """A freshly migrated throwaway database, dropped afterwards."""
    admin_dsn = _admin_dsn()
    db_name = f"luthien_test_history_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{db_name}"')
    await admin.close()

    test_dsn = admin_dsn.rsplit("/", 1)[0] + f"/{db_name}"
    conn = await asyncpg.connect(test_dsn)
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if migration.name.startswith("000"):  # creates roles/databases; superuser-only
            continue
        await conn.execute(migration.read_text())
    await conn.close()

    pool = DatabasePool(test_dsn, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE "{db_name}"')
        await admin.close()


async def _record(
    conn: ConnectionProtocol,
    *,
    call_id: str,
    session_id: str,
    user_id: str,
    event_type: str,
    payload: dict[str, Any],
    at: datetime,
) -> None:
    """Write one event the way ``observability.emitter`` does: call row, event row, summary upsert."""
    await conn.execute(
        """
        INSERT INTO conversation_calls (call_id, created_at, session_id, user_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (call_id) DO UPDATE SET
            session_id = COALESCE(conversation_calls.session_id, EXCLUDED.session_id),
            user_id = COALESCE(conversation_calls.user_id, EXCLUDED.user_id)
        """,
        call_id,
        at,
        session_id,
        user_id,
    )
    await conn.execute(
        """
        INSERT INTO conversation_events (call_id, event_type, payload, created_at, session_id)
        VALUES ($1, $2, $3, $4, $5)
        """,
        call_id,
        event_type,
        json.dumps(payload),
        at,
        session_id,
    )
    await update_session_summary(
        conn, session_id=session_id, event_type=event_type, data=payload, user_id=user_id, timestamp=at
    )


def _request(model: str, text: str, *, max_tokens: int = 1024) -> dict[str, Any]:
    return {
        "final_model": model,
        "final_request": {"max_tokens": max_tokens, "messages": [{"role": "user", "content": text}]},
    }


async def _seed_session(
    conn: ConnectionProtocol, *, session_id: str, user_id: str, model: str, start: datetime, events: int
) -> None:
    """``events`` events across ``events // 2`` calls: request + response per call."""
    for i in range(events):
        call_id = f"{session_id}-call-{i // 2}"
        at = start + timedelta(minutes=i)
        if i % 2 == 0:
            payload = _request(model, f"{session_id} question {i // 2}")
            event_type = "transaction.request_recorded"
        else:
            payload = {"final_response": {"choices": [{"message": {"content": "answer"}}]}}
            event_type = "transaction.non_streaming_response_recorded"
        await _record(
            conn, call_id=call_id, session_id=session_id, user_id=user_id, event_type=event_type, payload=payload, at=at
        )


async def _seed(pool: DatabasePool) -> None:
    april = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    march = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    async with pool.connection() as conn, conn.transaction():
        for offset, session_id in enumerate(reversed(APRIL_SESSIONS)):
            await _seed_session(
                conn,
                session_id=session_id,
                user_id=ALICE,
                model=OPUS,
                start=april + timedelta(days=offset),
                events=EVENTS_PER_APRIL_SESSION,
            )
        # A probe (max_tokens=1) ahead of the real first message: preview must skip it.
        await _record(
            conn,
            call_id="alice-april-1-probe",
            session_id="alice-april-1",
            user_id=ALICE,
            event_type="transaction.request_recorded",
            payload=_request(OPUS, "quota probe", max_tokens=1),
            at=april - timedelta(minutes=1),
        )
        await _record(
            conn,
            call_id="alice-april-1-call-0",
            session_id="alice-april-1",
            user_id=ALICE,
            event_type="policy.anthropic_judge.tool_call_blocked",
            payload={"summary": "blocked"},
            at=april + timedelta(minutes=1, seconds=30),
        )
        for n in range(ALICE_MARCH_SESSIONS):
            await _seed_session(
                conn,
                session_id=f"alice-march-{n}",
                user_id=ALICE,
                model=OPUS,
                start=march + timedelta(hours=n),
                events=ALICE_MARCH_EVENTS_PER_SESSION,
            )
        for n in range(BOB_SESSIONS):
            await _seed_session(
                conn,
                session_id=f"bob-{n}",
                user_id=BOB,
                model=GPT,
                start=april + timedelta(hours=n),
                events=BOB_EVENTS_PER_SESSION,
            )
    async with pool.connection() as conn:
        await conn.execute("ANALYZE conversation_events")
        await conn.execute("ANALYZE conversation_calls")
        await conn.execute("ANALYZE session_summaries")


class _RecordingConn:
    """Delegates to a real connection and records every ``fetch``/``fetchval``."""

    def __init__(self, inner: ConnectionProtocol, queries: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._inner = inner
        self._queries = queries

    async def fetch(self, query: str, *args: Any) -> Any:
        self._queries.append((query, args))
        return await self._inner.fetch(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        self._queries.append((query, args))
        return await self._inner.fetchval(query, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingPool:
    is_sqlite = False
    is_postgres = True

    def __init__(self, inner: DatabasePool) -> None:
        self._inner = inner
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_RecordingConn]:
        async with self._inner.connection() as conn:
            yield _RecordingConn(conn, self.queries)


def _events_rows_touched(plan: dict[str, Any]) -> int:
    """Rows the plan read from ``conversation_events`` (output + filtered away), all loops."""
    touched = 0
    if plan.get("Relation Name") == "conversation_events":
        per_loop = (
            plan.get("Actual Rows", 0)
            + plan.get("Rows Removed by Filter", 0)
            + plan.get("Rows Removed by Index Recheck", 0)
        )
        touched += per_loop * plan.get("Actual Loops", 1)
    for child in plan.get("Plans", []):
        touched += _events_rows_touched(child)
    return touched


async def _rows_touched_by(pool: DatabasePool, queries: list[tuple[str, tuple[Any, ...]]]) -> int:
    total = 0
    async with pool.connection() as conn:
        for sql, args in queries:
            explained = await conn.fetchval(f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}", *args)
            root = json.loads(cast(str, explained))[0]["Plan"]
            total += _events_rows_touched(root)
    return total


@pytest.mark.integration
@pytest.mark.timeout(120)
class TestFilteredSessionListIsBoundedToThePage:
    @pytest.mark.asyncio
    async def test_user_scoped_time_range_reads_only_the_pages_events(self, pg_pool: DatabasePool) -> None:
        await _seed(pg_pool)
        recording = _RecordingPool(pg_pool)
        search = SessionSearchParams(from_time=datetime(2026, 4, 1), to_time=datetime(2026, 4, 30, 23, 59, 59))

        page = await fetch_session_list(2, cast(DatabasePool, recording), 0, user_id=ALICE, search=search)

        assert [s.session_id for s in page.sessions] == APRIL_SESSIONS[:2]
        assert page.total == len(APRIL_SESSIONS)
        assert page.has_more is True
        touched = await _rows_touched_by(pg_pool, recording.queries)
        # Four reads of the page's events (stats, models, first message, user_ids); nothing else
        # touches conversation_events — the count and the candidate gate run on summaries + calls.
        page_events = 2 * EVENTS_PER_APRIL_SESSION
        assert touched <= 4 * page_events, f"read {touched} conversation_events rows for a {page_events}-event page"

    @pytest.mark.asyncio
    async def test_model_filter_reads_only_matching_users_sessions(self, pg_pool: DatabasePool) -> None:
        await _seed(pg_pool)
        recording = _RecordingPool(pg_pool)
        search = SessionSearchParams(
            model=OPUS, from_time=datetime(2026, 4, 1), to_time=datetime(2026, 4, 30, 23, 59, 59)
        )

        page = await fetch_session_list(2, cast(DatabasePool, recording), 0, user_id=ALICE, search=search)

        assert [s.session_id for s in page.sessions] == APRIL_SESSIONS[:2]
        assert page.total == len(APRIL_SESSIONS)
        touched = await _rows_touched_by(pg_pool, recording.queries)
        # The model gate is answered from alice's own events (per-candidate probe or the
        # final_model index joined to her calls — the planner's choice), once for the count and
        # once for the page; the four page reads follow. Bob's 5,000 events are never read.
        alice_events = (
            len(APRIL_SESSIONS) * EVENTS_PER_APRIL_SESSION + 2 + ALICE_MARCH_SESSIONS * ALICE_MARCH_EVENTS_PER_SESSION
        )
        page_events = 2 * EVENTS_PER_APRIL_SESSION
        bound = 2 * alice_events + 4 * page_events
        assert touched <= bound, f"read {touched} conversation_events rows; bound {bound}"

    @pytest.mark.asyncio
    async def test_filtered_page_reports_exact_session_stats(self, pg_pool: DatabasePool) -> None:
        await _seed(pg_pool)
        search = SessionSearchParams(model=OPUS, from_time=datetime(2026, 4, 1), policy_intervention=True)

        page = await fetch_session_list(10, pg_pool, 0, user_id=ALICE, search=search)

        assert page.total == 1
        (session,) = page.sessions
        assert session.session_id == "alice-april-1"
        assert session.turn_count == 3  # two real calls + the probe call
        assert session.total_events == EVENTS_PER_APRIL_SESSION + 2
        assert session.policy_interventions == 1
        assert session.models_used == [OPUS]
        assert session.preview_message == "alice-april-1 question 0"
        assert session.user_ids == [ALICE]
        assert session.first_timestamp == "2026-04-10T11:59:00+00:00"
        assert session.last_timestamp == "2026-04-10T12:03:00+00:00"

    @pytest.mark.asyncio
    async def test_second_page_continues_where_the_first_ended(self, pg_pool: DatabasePool) -> None:
        await _seed(pg_pool)
        search = SessionSearchParams(from_time=datetime(2026, 4, 1), to_time=datetime(2026, 4, 30, 23, 59, 59))

        first = await fetch_session_list(2, pg_pool, 0, user_id=ALICE, search=search)
        second = await fetch_session_list(2, pg_pool, 2, user_id=ALICE, search=search)

        assert [s.session_id for s in first.sessions] + [s.session_id for s in second.sessions] == APRIL_SESSIONS
        assert second.has_more is False

    @pytest.mark.asyncio
    async def test_full_text_q_matches_via_search_vector_and_respects_user_scope(self, pg_pool: DatabasePool) -> None:
        """``q`` runs through the tsvector index maintained by migration 014's trigger."""
        await _seed(pg_pool)

        everyone = await fetch_session_list(10, pg_pool, 0, search=SessionSearchParams(q="alice-april-2 question"))
        assert [s.session_id for s in everyone.sessions] == ["alice-april-2"]

        # Bob's text never qualifies a session for alice, even under a matching term.
        bobs_words = await fetch_session_list(
            10, pg_pool, 0, user_id=ALICE, search=SessionSearchParams(q="bob-7 question")
        )
        assert bobs_words.sessions == []
        assert bobs_words.total == 0

    @pytest.mark.asyncio
    async def test_shared_session_stats_are_scoped_to_the_requesting_user(self, pg_pool: DatabasePool) -> None:
        """A session_id shared by two users shows each user only their own calls."""
        await _seed(pg_pool)
        async with pg_pool.connection() as conn:
            await _record(
                conn,
                call_id="bob-in-alices-session",
                session_id="alice-april-3",
                user_id=BOB,
                event_type="transaction.request_recorded",
                payload=_request(GPT, "BOBS PRIVATE MESSAGE"),
                at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),  # newest activity of all: tops both lists
            )

        as_alice = await fetch_session_list(1, pg_pool, 0, user_id=ALICE)
        (session,) = as_alice.sessions
        assert session.session_id == "alice-april-3"
        assert session.user_ids == [ALICE]
        assert session.models_used == [OPUS]
        assert session.total_events == EVENTS_PER_APRIL_SESSION
        assert session.preview_message == "alice-april-3 question 0"

        as_bob = await fetch_session_list(1, pg_pool, 0, user_id=BOB)
        (session,) = as_bob.sessions
        assert session.session_id == "alice-april-3"
        assert session.user_ids == [BOB]
        assert session.models_used == [GPT]
        assert session.total_events == 1
        assert session.preview_message == "BOBS PRIVATE MESSAGE"
