from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import anyio
import pytest

import luthien_proxy.passthrough_materialize.materialize as materialize_module
import luthien_proxy.passthrough_materialize.reconcile as reconcile_module
from luthien_proxy.passthrough_materialize.materialize import materialize_transaction
from luthien_proxy.passthrough_materialize.materialize_types import (
    AlreadyMaterialized,
    CanonicalTransaction,
    MaterializationResult,
    Materialized,
    ReconcileStats,
)
from luthien_proxy.passthrough_materialize.reconcile import reconcile_passthrough
from luthien_proxy.utils.db import DatabasePool

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class _PostgresSeed:
    transaction_id: str
    session_id: str


@pytest.fixture
async def postgres_pool() -> AsyncIterator[DatabasePool]:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url or database_url.startswith("sqlite"):
        pytest.skip("DATABASE_URL is not configured for a Postgres integration database")
    pool = DatabasePool(database_url, min_size=2, max_size=2)
    try:
        async with pool.connection() as conn:
            await conn.fetchval("SELECT 1")
        yield pool
    finally:
        await pool.close()


async def _seed_request_log(pool: DatabasePool, seed: _PostgresSeed) -> None:
    started_at = datetime.now(timezone.utc)
    request_body = {
        "model": "gpt-4.1",
        "messages": [{"role": "user", "content": "race"}],
    }
    response_body = {
        "id": f"chatcmpl-{seed.transaction_id}",
        "model": "gpt-4.1",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "raced"}}],
    }
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
            seed.session_id,
            "postgres-race-user",
            "inbound",
            json.dumps(request_body),
            200,
            json.dumps(response_body),
            started_at,
            started_at,
            "gpt-4.1",
            False,
            "/openai/v1/chat/completions",
            None,
        )


async def _delete_seed(pool: DatabasePool, seed: _PostgresSeed) -> None:
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM session_summaries WHERE session_id = $1", seed.session_id)
        await conn.execute("DELETE FROM conversation_calls WHERE call_id = $1", seed.transaction_id)
        await conn.execute("DELETE FROM request_logs WHERE transaction_id = $1", seed.transaction_id)


async def test_postgres_reconcile_and_live_materialization_race_preserves_one_canonical_turn(
    postgres_pool: DatabasePool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _PostgresSeed(
        transaction_id=f"passthrough-race-{uuid4().hex}",
        session_id=f"session-passthrough-race-{uuid4().hex}",
    )
    await _seed_request_log(postgres_pool, seed)
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
            with anyio.fail_after(5):
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
        reconcile_results.append(await reconcile_passthrough(postgres_pool, limit=1))

    async def run_live() -> None:
        live_results.append(await materialize_transaction(postgres_pool, seed.transaction_id))

    monkeypatch.setattr(materialize_module, "write_canonical_transaction", synchronized_write)
    monkeypatch.setattr(reconcile_module, "materialize_transaction", reconcile_materialize)

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_reconcile)
            with anyio.fail_after(5):
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
        async with postgres_pool.connection() as conn:
            call_count = await conn.fetchval(
                "SELECT COUNT(*) FROM conversation_calls WHERE call_id = $1", seed.transaction_id
            )
            event_count = await conn.fetchval(
                "SELECT COUNT(*) FROM conversation_events WHERE call_id = $1", seed.transaction_id
            )
            summary_count = await conn.fetchval(
                "SELECT COUNT(*) FROM session_summaries WHERE session_id = $1", seed.session_id
            )
            summary = await conn.fetchrow("SELECT * FROM session_summaries WHERE session_id = $1", seed.session_id)

        assert call_count == 1
        assert event_count == 2
        assert summary_count == 1
        assert summary is not None
        assert summary["event_count"] == 2
        assert summary["call_count"] == 1
        assert summary["models_used"] == "gpt-4.1"
    finally:
        await _delete_seed(postgres_pool, seed)
