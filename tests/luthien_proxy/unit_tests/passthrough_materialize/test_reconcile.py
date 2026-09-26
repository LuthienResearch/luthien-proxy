from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import datetime

import aiosqlite
import anyio
import pytest

import luthien_proxy.passthrough_materialize.materialize as materialize_module
import luthien_proxy.passthrough_materialize.reconcile as reconcile_module
from luthien_proxy.passthrough_materialize.materialize import materialize_transaction
from luthien_proxy.passthrough_materialize.materialize_types import (
    AlreadyMaterialized,
    CanonicalTransaction,
    MaterializationFailed,
    MaterializationResult,
    Materialized,
    ReconcileStats,
)
from luthien_proxy.passthrough_materialize.payloads import JsonObject
from luthien_proxy.passthrough_materialize.reconcile import reconcile_passthrough
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations


@pytest.fixture
async def reconcile_pool() -> AsyncIterator[DatabasePool]:
    pool = DatabasePool("sqlite://:memory:")
    await check_migrations(pool)
    yield pool
    await pool.close()


@dataclass(frozen=True, slots=True)
class _RawLogSeed:
    transaction_id: str
    started_at: datetime
    endpoint: str
    request_body: JsonObject
    response_body: JsonObject


def _at(hour: int) -> datetime:
    return datetime.fromisoformat(f"2026-07-11T{hour:02d}:00:00+00:00")


def _valid_openai_seed(transaction_id: str, started_at: datetime) -> _RawLogSeed:
    return _RawLogSeed(
        transaction_id=transaction_id,
        started_at=started_at,
        endpoint="/openai/v1/chat/completions",
        request_body={
            "model": "gpt-4.1",
            "messages": [{"role": "user", "content": f"Hello from {transaction_id}."}],
        },
        response_body={
            "id": f"chatcmpl-{transaction_id}",
            "model": "gpt-4.1",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Hello."}}],
        },
    )


async def _seed_raw_log(pool: DatabasePool, seed: _RawLogSeed) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO request_logs (
                id, transaction_id, session_id, user_id, direction, request_body,
                response_status, response_body, started_at, completed_at, model,
                is_streaming, endpoint, error
            ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9, $10, $11, $12, $13, $14)
            """,
            f"log-{seed.transaction_id}",
            seed.transaction_id,
            f"session-{seed.transaction_id}",
            f"user-{seed.transaction_id}",
            "inbound",
            json.dumps(seed.request_body),
            200,
            json.dumps(seed.response_body),
            seed.started_at,
            seed.started_at,
            "gpt-4.1",
            False,
            seed.endpoint,
            None,
        )


async def _event_count(pool: DatabasePool, transaction_id: str) -> int:
    async with pool.connection() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM conversation_events WHERE call_id = $1", transaction_id)
    assert isinstance(count, int)
    return count


async def _dead_letter_reason(pool: DatabasePool, transaction_id: str) -> str | None:
    async with pool.connection() as conn:
        row = await conn.fetchrow(
            "SELECT reason FROM passthrough_materialization_dead_letters WHERE transaction_id = $1", transaction_id
        )
    return None if row is None else str(row["reason"])


async def test_reconcile_materializes_only_eligible_unmaterialized_transactions_and_converges_next_sweep(
    reconcile_pool: DatabasePool,
) -> None:
    # Given
    ready = _valid_openai_seed("ready", _at(9))
    already = _valid_openai_seed("already", _at(10))
    ineligible = replace(_valid_openai_seed("ineligible", _at(11)), endpoint="/openai/v1/models")
    failure = replace(_valid_openai_seed("failure", _at(12)), request_body={"model": "gpt-4.1"})
    await _seed_raw_log(reconcile_pool, ready)
    await _seed_raw_log(reconcile_pool, already)
    await _seed_raw_log(reconcile_pool, ineligible)
    await _seed_raw_log(reconcile_pool, failure)
    await materialize_transaction(reconcile_pool, already.transaction_id)

    # When
    first_pass = await reconcile_passthrough(reconcile_pool, limit=10)
    second_pass = await reconcile_passthrough(reconcile_pool, limit=10)

    # Then
    assert first_pass == ReconcileStats(materialized=1, failed=1)
    # The permanent failure ("failure") was dead-lettered on the first pass, so
    # the second pass -- run over the exact same rows -- selects nothing new.
    # Before the fix this asserted ReconcileStats(failed=1): the same broken
    # transaction was re-selected and re-failed forever.
    assert second_pass == ReconcileStats()
    assert await _event_count(reconcile_pool, ready.transaction_id) == 2
    assert await _event_count(reconcile_pool, already.transaction_id) == 2
    assert await _event_count(reconcile_pool, ineligible.transaction_id) == 0
    assert await _event_count(reconcile_pool, failure.transaction_id) == 0
    assert await _dead_letter_reason(reconcile_pool, failure.transaction_id) == "missing_required_field"
    assert await _dead_letter_reason(reconcile_pool, ready.transaction_id) is None


async def test_reconcile_converges_when_the_entire_backlog_is_permanently_broken(
    reconcile_pool: DatabasePool,
) -> None:
    """Reproduces the production bug: every eligible row fails identically forever.

    Before persisting dead letters, a sweep over an all-permanently-broken backlog
    re-selected and re-failed the exact same rows on every subsequent sweep -- the
    root cause of PassthroughReconcileWorker running forever with zero progress.
    """
    # Given
    broken_seeds = [
        replace(_valid_openai_seed(f"broken-{i}", _at(9)), request_body={"model": "gpt-4.1"}) for i in range(5)
    ]
    for seed in broken_seeds:
        await _seed_raw_log(reconcile_pool, seed)

    # When
    first_pass = await reconcile_passthrough(reconcile_pool, limit=10)
    second_pass = await reconcile_passthrough(reconcile_pool, limit=10)

    # Then
    assert first_pass == ReconcileStats(failed=5)
    assert second_pass == ReconcileStats()
    async with reconcile_pool.connection() as conn:
        dead_letter_count = await conn.fetchval("SELECT COUNT(*) FROM passthrough_materialization_dead_letters")
    assert dead_letter_count == 5


async def test_reconcile_leaves_a_transient_failure_reason_eligible_for_retry(
    reconcile_pool: DatabasePool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    transient = _valid_openai_seed("transient", _at(9))
    await _seed_raw_log(reconcile_pool, transient)
    original_materialize = reconcile_module.materialize_transaction

    async def materialize_with_transient_failure(pool: DatabasePool, transaction_id: str) -> MaterializationResult:
        if transaction_id == transient.transaction_id:
            return MaterializationFailed(transaction_id=transaction_id, reason="missing_request_logs")
        return await original_materialize(pool, transaction_id)

    monkeypatch.setattr(reconcile_module, "materialize_transaction", materialize_with_transient_failure)

    # When
    first_pass = await reconcile_passthrough(reconcile_pool, limit=10)
    second_pass = await reconcile_passthrough(reconcile_pool, limit=10)

    # Then: unlike a permanent failure, a "missing_request_logs" failure is never
    # dead-lettered, so it is re-selected (and re-fails) on every sweep.
    assert first_pass == ReconcileStats(failed=1)
    assert second_pass == ReconcileStats(failed=1)
    assert await _dead_letter_reason(reconcile_pool, transient.transaction_id) is None


async def test_migration_creates_the_dead_letter_table_and_supporting_index(reconcile_pool: DatabasePool) -> None:
    """Both the Postgres and SQLite 023 migrations must produce this schema.

    This exercises the SQLite side (used by every fixture in this file via
    check_migrations); the Postgres side was verified against a locally seeded
    multi-million-row table (see PR description for EXPLAIN evidence).
    """
    async with reconcile_pool.connection() as conn:
        table = await conn.fetchrow(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'passthrough_materialization_dead_letters'"
        )
        index = await conn.fetchrow(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_request_logs_passthrough_eligible'"
        )
    assert table is not None
    assert index is not None


async def test_reconcile_respects_oldest_first_limit_and_since_when_selecting_transactions(
    reconcile_pool: DatabasePool,
) -> None:
    # Given
    before_window = _valid_openai_seed("before-window", _at(9))
    oldest = _valid_openai_seed("oldest", _at(10))
    middle = _valid_openai_seed("middle", _at(11))
    newest = _valid_openai_seed("newest", _at(12))
    await _seed_raw_log(reconcile_pool, before_window)
    await _seed_raw_log(reconcile_pool, oldest)
    await _seed_raw_log(reconcile_pool, middle)
    await _seed_raw_log(reconcile_pool, newest)

    # When
    limited = await reconcile_passthrough(reconcile_pool, limit=1, since=_at(10))
    filtered = await reconcile_passthrough(reconcile_pool, limit=10, since=_at(11))

    # Then
    assert limited == ReconcileStats(materialized=1)
    assert filtered == ReconcileStats(materialized=2)
    assert await _event_count(reconcile_pool, before_window.transaction_id) == 0
    assert await _event_count(reconcile_pool, oldest.transaction_id) == 2
    assert await _event_count(reconcile_pool, middle.transaction_id) == 2
    assert await _event_count(reconcile_pool, newest.transaction_id) == 2


async def test_reconcile_counts_a_database_error_and_continues_with_later_transactions(
    reconcile_pool: DatabasePool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _valid_openai_seed("ready-after-error", _at(10))
    broken = _valid_openai_seed("broken", _at(9))
    await _seed_raw_log(reconcile_pool, ready)
    await _seed_raw_log(reconcile_pool, broken)
    original_materialize = reconcile_module.materialize_transaction

    async def materialize_with_database_error(pool: DatabasePool, transaction_id: str) -> MaterializationResult:
        if transaction_id == broken.transaction_id:
            raise aiosqlite.OperationalError("database unavailable")
        return await original_materialize(pool, transaction_id)

    monkeypatch.setattr(reconcile_module, "materialize_transaction", materialize_with_database_error)

    stats = await reconcile_passthrough(reconcile_pool, limit=10)

    assert stats == ReconcileStats(materialized=1, failed=1)
    assert await _event_count(reconcile_pool, ready.transaction_id) == 2
    assert await _event_count(reconcile_pool, broken.transaction_id) == 0


async def test_reconcile_and_live_materialization_produce_one_canonical_turn_when_they_contend(
    reconcile_pool: DatabasePool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction = _valid_openai_seed("contended", _at(10))
    await _seed_raw_log(reconcile_pool, transaction)
    reconcile_started = anyio.Event()
    both_writers_ready = anyio.Event()
    writer_count = 0
    reconcile_results: list[ReconcileStats] = []
    live_results: list[MaterializationResult] = []
    original_write = materialize_module.write_canonical_transaction
    original_reconcile_materialize = reconcile_module.materialize_transaction

    async def synchronized_write(
        pool: DatabasePool, canonical: CanonicalTransaction
    ) -> Materialized | AlreadyMaterialized:
        nonlocal writer_count
        writer_count += 1
        if writer_count == 1:
            with anyio.fail_after(1):
                await both_writers_ready.wait()
        elif writer_count == 2:
            both_writers_ready.set()
        else:
            raise AssertionError("expected exactly two concurrent materialization writers")
        return await original_write(pool, canonical)

    async def reconcile_materialize(pool: DatabasePool, transaction_id: str) -> MaterializationResult:
        reconcile_started.set()
        return await original_reconcile_materialize(pool, transaction_id)

    async def run_reconcile() -> None:
        reconcile_results.append(await reconcile_passthrough(reconcile_pool, limit=1))

    async def run_live() -> None:
        live_results.append(await materialize_transaction(reconcile_pool, transaction.transaction_id))

    monkeypatch.setattr(materialize_module, "write_canonical_transaction", synchronized_write)
    monkeypatch.setattr(reconcile_module, "materialize_transaction", reconcile_materialize)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_reconcile)
        with anyio.fail_after(1):
            await reconcile_started.wait()
        task_group.start_soon(run_live)

    assert len(reconcile_results) == 1
    assert len(live_results) == 1
    reconcile_stats = reconcile_results[0]
    live_result = live_results[0]
    assert reconcile_stats.failed == 0
    assert reconcile_stats.skipped_ineligible == 0
    assert reconcile_stats.materialized + reconcile_stats.already_materialized == 1
    assert isinstance(live_result, Materialized | AlreadyMaterialized)
    async with reconcile_pool.connection() as conn:
        call_count = await conn.fetchval(
            "SELECT COUNT(*) FROM conversation_calls WHERE call_id = $1", transaction.transaction_id
        )
        event_count = await conn.fetchval(
            "SELECT COUNT(*) FROM conversation_events WHERE call_id = $1", transaction.transaction_id
        )
        summary = await conn.fetchrow("SELECT * FROM session_summaries WHERE session_id = $1", "session-contended")

    assert call_count == 1
    assert event_count == 2
    assert summary is not None
    assert summary["event_count"] == 2
    assert summary["call_count"] == 1
    assert summary["models_used"] == "gpt-4.1"
