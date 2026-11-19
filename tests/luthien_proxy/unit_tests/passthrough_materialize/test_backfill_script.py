from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from luthien_proxy.passthrough_materialize.backfill import drain_passthrough_backfill
from luthien_proxy.passthrough_materialize.materialize_types import ReconcileStats
from luthien_proxy.passthrough_materialize.reconcile import reconcile_passthrough
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations


@pytest.fixture
async def backfill_pool() -> AsyncIterator[DatabasePool]:
    pool = DatabasePool("sqlite://:memory:")
    await check_migrations(pool)
    yield pool
    await pool.close()


async def _seed_openai_transaction(pool: DatabasePool, transaction_id: str, started_at: str) -> None:
    request_body = {
        "model": "gpt-4.1",
        "messages": [{"role": "user", "content": f"Hello from {transaction_id}."}],
    }
    response_body = {
        "id": f"chatcmpl-{transaction_id}",
        "model": "gpt-4.1",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Hello."}}],
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
            f"log-{transaction_id}",
            transaction_id,
            f"session-{transaction_id}",
            f"user-{transaction_id}",
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


async def _event_count(pool: DatabasePool, transaction_id: str) -> int:
    async with pool.connection() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM conversation_events WHERE call_id = $1", transaction_id)
    assert isinstance(count, int)
    return count


async def test_drain_backfill_materializes_every_eligible_transaction_then_stops_at_an_empty_sweep(
    backfill_pool: DatabasePool,
) -> None:
    # Given
    await _seed_openai_transaction(backfill_pool, "first", "2026-07-11T09:00:00+00:00")
    await _seed_openai_transaction(backfill_pool, "second", "2026-07-11T10:00:00+00:00")

    # When
    totals = await drain_passthrough_backfill(backfill_pool, limit=1)

    # Then
    assert totals == ReconcileStats(materialized=2)
    assert await _event_count(backfill_pool, "first") == 2
    assert await _event_count(backfill_pool, "second") == 2
    assert await reconcile_passthrough(backfill_pool, limit=1) == ReconcileStats()
