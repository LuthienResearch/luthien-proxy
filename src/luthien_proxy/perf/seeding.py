"""Direct-SQL seeding module for perf-test database.

Inserts production-shaped rows into conversation_calls and conversation_events
for performance benchmarking. Uses direct sqlite3 connections and executemany
for maximum throughput.

All session_ids are prefixed with 'perf-seed-{tier}-' or 'perf-seed-sami-'.
IDs are fully deterministic — drop + re-seed produces identical data.

FK ordering: conversation_calls rows are inserted before conversation_events rows.
"""

from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from luthien_proxy.perf.db import ensure_perf_isolation, get_perf_db_url, migrate_perf_db

_MODEL = "claude-haiku-4-5"
_BASE_TS = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_BATCH_SIZE = 5000

_CALLS_INSERT = (
    "INSERT INTO conversation_calls"
    " (call_id, model_name, provider, status, created_at, completed_at, session_id)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_EVENTS_INSERT = (
    "INSERT INTO conversation_events"
    " (id, call_id, event_type, payload, created_at, session_id)"
    " VALUES (?, ?, ?, ?, ?, ?)"
)

# Pre-built JSON template fragments — content is pure ASCII, no escaping needed.
_REQ_PAD = "A" * 50
_RESP_PAD = "B" * 100

_REQ_HEAD = (
    '{"final_request": {"model": "' + _MODEL + '", "max_tokens": 1024,'
    ' "stream": true, "temperature": 0.7,'
    ' "messages": [{"role": "user", "content": "'
)
_REQ_MID = (
    '"}]}, "original_request": {"model": "' + _MODEL + '", "max_tokens": 1024,'
    ' "stream": true, "temperature": 0.7,'
    ' "messages": [{"role": "user", "content": "'
)
_REQ_TAIL = '"}]}, "final_model": "' + _MODEL + '"}'

_RESP_HEAD = (
    '{"final_response": {"id": "msg_000000", "type": "message",'
    ' "role": "assistant", "model": "' + _MODEL + '",'
    ' "stop_reason": "end_turn", "stop_sequence": null,'
    ' "usage": {"input_tokens": 256, "output_tokens": 512},'
    ' "content": [{"type": "text", "text": "'
)
_RESP_TAIL = '"}]}}'


@dataclass(frozen=True)
class SeedingReport:
    """Report returned by seeding functions with metrics about the seeding run."""

    tier: int | str
    total_sessions: int
    total_rows: int
    total_bytes: int
    elapsed_seconds: float
    backend: str
    biggest_session_message_count: int


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _req_payload(session_id: str, call_idx: int) -> str:
    """~5 KB JSON string for a transaction.request_recorded event."""
    content = f"s={session_id[:12]} c={call_idx:04d} " + _REQ_PAD
    return _REQ_HEAD + content + _REQ_MID + content + _REQ_TAIL


def _resp_payload(session_id: str, call_idx: int) -> str:
    """~20 KB JSON string for a transaction.streaming_response_recorded event."""
    text = f"r={session_id[:12]} c={call_idx:04d} " + _RESP_PAD
    return _RESP_HEAD + text + _RESP_TAIL


def _call_count(session_idx: int, rng_seed: int) -> int:
    """Deterministic call count per session.

    Distribution (in calls; each call = 2 events):
    - 50% → 5–15 calls  (10–30 events; median ≈ 20 events)
    - 45% → 15–50 calls (30–100 events; p95 ≈ 100 events)
    - 5%  → 50–250 calls (100–500 events; p99 ≈ 500 events)
    """
    rng = random.Random(rng_seed * 1_000_003 + session_idx)
    r = rng.random()
    if r < 0.50:
        return rng.randint(5, 15)
    elif r < 0.95:
        return rng.randint(15, 50)
    else:
        return rng.randint(50, 250)


def _sqlite_path(url: str) -> Path:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"Expected sqlite:/// URL, got {url!r}")
    return Path(url[len(prefix) :])


def _seed_sqlite(
    db_path: Path,
    plan: list[tuple[str, int]],
    tier: int | str,
    backend: str = "sqlite",
) -> SeedingReport:
    """Bulk-insert plan into SQLite via executemany.

    Args:
        db_path: Path to the SQLite database file.
        plan: List of (session_id, n_calls) pairs.
        tier: Tier label for the report.
        backend: Backend label for the report.

    Returns:
        SeedingReport with insertion statistics.
    """
    t0 = time.monotonic()
    total_bytes = 0
    biggest = 0

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")  # intentionally unsafe — perf DB is disposable
    conn.execute("PRAGMA cache_size=-131072")
    conn.execute("PRAGMA temp_store=MEMORY")

    try:
        # Drop indexes before bulk insert — dramatically reduces write amplification.
        # Indexes are recreated after all rows are inserted.
        for idx in (
            "idx_conversation_events_type",
            "idx_conversation_events_created",
            "idx_conversation_events_call_created",
            "idx_conversation_events_session",
            "idx_conversation_calls_created",
            "idx_conversation_calls_session",
            "idx_conversation_calls_user",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")

        # Pass 1: conversation_calls (FK parent) — must precede events.
        calls_batch: list[tuple] = []
        for session_idx, (session_id, n_calls) in enumerate(plan):
            if n_calls > biggest:
                biggest = n_calls
            for call_idx in range(n_calls):
                call_id = f"{session_id}-{call_idx:04d}"
                ts = _fmt_ts(_BASE_TS + timedelta(seconds=session_idx * 3600 + call_idx * 5))
                calls_batch.append((call_id, _MODEL, "anthropic", "completed", ts, ts, session_id))
                if len(calls_batch) >= _BATCH_SIZE:
                    conn.executemany(_CALLS_INSERT, calls_batch)
                    calls_batch.clear()
        if calls_batch:
            conn.executemany(_CALLS_INSERT, calls_batch)

        # Pass 2: conversation_events (FK child).
        events_batch: list[tuple] = []
        for session_idx, (session_id, n_calls) in enumerate(plan):
            for call_idx in range(n_calls):
                call_id = f"{session_id}-{call_idx:04d}"
                ts_req = _fmt_ts(_BASE_TS + timedelta(seconds=session_idx * 3600 + call_idx * 5))
                ts_resp = _fmt_ts(_BASE_TS + timedelta(seconds=session_idx * 3600 + call_idx * 5 + 1))
                req_p = _req_payload(session_id, call_idx)
                resp_p = _resp_payload(session_id, call_idx)
                total_bytes += len(req_p) + len(resp_p)

                events_batch.append(
                    (
                        f"{call_id}-req",
                        call_id,
                        "transaction.request_recorded",
                        req_p,
                        ts_req,
                        session_id,
                    )
                )
                events_batch.append(
                    (
                        f"{call_id}-resp",
                        call_id,
                        "transaction.streaming_response_recorded",
                        resp_p,
                        ts_resp,
                        session_id,
                    )
                )

                if len(events_batch) >= _BATCH_SIZE:
                    conn.executemany(_EVENTS_INSERT, events_batch)
                    events_batch.clear()

        if events_batch:
            conn.executemany(_EVENTS_INSERT, events_batch)

        # Recreate indexes after bulk insert.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_events_type ON conversation_events(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_events_created ON conversation_events(created_at)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_events_call_created"
            " ON conversation_events(call_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_events_session"
            " ON conversation_events(session_id) WHERE session_id IS NOT NULL"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_calls_created ON conversation_calls(created_at)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_calls_session"
            " ON conversation_calls(session_id) WHERE session_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_calls_user"
            " ON conversation_calls(user_id) WHERE user_id IS NOT NULL"
        )
        conn.commit()
    finally:
        conn.close()

    elapsed = time.monotonic() - t0
    n_calls_total = sum(n for _, n in plan)
    total_rows = n_calls_total + 2 * n_calls_total  # calls + 2 events per call

    return SeedingReport(
        tier=tier,
        total_sessions=len(plan),
        total_rows=total_rows,
        total_bytes=total_bytes,
        elapsed_seconds=elapsed,
        backend=backend,
        biggest_session_message_count=biggest,
    )


def seed_sessions(
    backend: Literal["sqlite", "postgres"],
    tier: int,
) -> SeedingReport:
    """Seed the perf database with ``tier`` sessions.

    Calls ensure_perf_isolation and migrate_perf_db before inserting.
    All session_ids are prefixed with ``perf-seed-{tier}-``.
    IDs are fully deterministic — drop + re-seed produces identical data.

    Args:
        backend: "sqlite" or "postgres".
        tier: Number of sessions to insert (typically 100, 1_000, or 10_000).

    Returns:
        SeedingReport with insertion statistics.
    """
    url = get_perf_db_url(backend)
    ensure_perf_isolation(url)
    migrate_perf_db(backend)

    prefix = f"perf-seed-{tier}-"
    plan = [(f"{prefix}{i:04d}", _call_count(i, rng_seed=tier)) for i in range(tier)]

    if backend == "sqlite":
        return _seed_sqlite(_sqlite_path(url), plan, tier=tier, backend=backend)
    raise NotImplementedError(f"backend {backend!r} not yet implemented")


def seed_sami_like(backend: Literal["sqlite", "postgres"]) -> SeedingReport:
    """Seed the perf database with a sami-like fixture.

    78 sessions total. Session ``perf-seed-sami-442msg`` has exactly 442 calls.
    Remaining 77 sessions have 1–187 calls (realistic spread).
    All session_ids are prefixed with ``perf-seed-sami-``.

    Args:
        backend: "sqlite" or "postgres".

    Returns:
        SeedingReport with biggest_session_message_count >= 442.
    """
    url = get_perf_db_url(backend)
    ensure_perf_isolation(url)
    migrate_perf_db(backend)

    prefix = "perf-seed-sami-"
    big_session_id = f"{prefix}442msg"

    rng = random.Random(0xABCDEF)
    other_plan: list[tuple[str, int]] = [(f"{prefix}{i:03d}", rng.randint(1, 187)) for i in range(77)]
    plan = [(big_session_id, 442)] + other_plan

    if backend == "sqlite":
        return _seed_sqlite(_sqlite_path(url), plan, tier="sami", backend=backend)
    raise NotImplementedError(f"backend {backend!r} not yet implemented")
