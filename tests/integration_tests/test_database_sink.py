# ABOUTME: Integration tests for DatabaseSink with real PostgreSQL
# ABOUTME: Tests concurrent write behavior to verify race condition fixes

"""Integration tests for DatabaseSink requiring a real PostgreSQL database.

These tests verify that concurrent writes to the same call_id produce
unique, sequential sequence numbers without race conditions.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from luthien_proxy.observability.context import PipelineRecord
from luthien_proxy.observability.sinks import DatabaseSink
from luthien_proxy.utils.db import DatabasePool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="Requires DATABASE_URL for PostgreSQL connection",
    ),
]


@pytest.fixture
async def db_pool():
    """Create a database pool for testing."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL not set")

    pool = DatabasePool(url=database_url)
    yield pool
    await pool.close()


@pytest.fixture
async def clean_test_call(db_pool: DatabasePool):
    """Provide a unique call_id and clean up after test."""
    call_id = f"test-concurrent-{uuid.uuid4()}"
    yield call_id

    # Cleanup: remove test data
    async with db_pool.connection() as conn:
        await conn.execute("DELETE FROM conversation_events WHERE call_id = $1", call_id)
        await conn.execute("DELETE FROM conversation_calls WHERE call_id = $1", call_id)


def make_test_record(call_id: str, index: int) -> PipelineRecord:
    """Create a test record for concurrent write testing."""
    return PipelineRecord(
        transaction_id=call_id,
        pipeline_stage="client_request",
        payload=f'{{"index": {index}}}',
    )


@pytest.mark.asyncio
async def test_concurrent_writes_produce_unique_sequences(db_pool: DatabasePool, clean_test_call: str):
    """Concurrent inserts for the same call_id should get unique sequence numbers.

    This test would fail with the old SELECT MAX + INSERT pattern due to
    race conditions, but passes with the FOR UPDATE locking approach.
    """
    sink = DatabaseSink(db_pool)
    call_id = clean_test_call
    num_concurrent = 20

    # Create many records to insert concurrently
    records = [make_test_record(call_id, i) for i in range(num_concurrent)]

    # Fire them all at once to maximize chance of race condition
    await asyncio.gather(*[sink.write(r) for r in records])

    # Check that all sequence numbers are unique and sequential
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            "SELECT sequence FROM conversation_events WHERE call_id = $1 ORDER BY sequence",
            call_id,
        )

    seq_values = [row["sequence"] for row in rows]

    # All records should have been inserted
    assert len(seq_values) == num_concurrent, f"Expected {num_concurrent} records, got {len(seq_values)}"

    # All sequence numbers should be unique
    assert len(set(seq_values)) == num_concurrent, f"Duplicate sequence numbers found: {seq_values}"

    # Sequences should be 1 through num_concurrent
    assert sorted(seq_values) == list(range(1, num_concurrent + 1)), (
        f"Sequences not contiguous 1-{num_concurrent}: {sorted(seq_values)}"
    )


@pytest.mark.asyncio
async def test_separate_call_ids_have_independent_sequences(
    db_pool: DatabasePool,
):
    """Different call_ids should have independent sequence numbering."""
    sink = DatabaseSink(db_pool)

    call_id_a = f"test-independent-a-{uuid.uuid4()}"
    call_id_b = f"test-independent-b-{uuid.uuid4()}"

    try:
        # Write to both call_ids concurrently
        records_a = [make_test_record(call_id_a, i) for i in range(5)]
        records_b = [make_test_record(call_id_b, i) for i in range(5)]

        await asyncio.gather(
            *[sink.write(r) for r in records_a],
            *[sink.write(r) for r in records_b],
        )

        # Check sequences for call_id_a
        async with db_pool.connection() as conn:
            rows_a = await conn.fetch(
                "SELECT sequence FROM conversation_events WHERE call_id = $1",
                call_id_a,
            )
            rows_b = await conn.fetch(
                "SELECT sequence FROM conversation_events WHERE call_id = $1",
                call_id_b,
            )

        seqs_a = sorted([row["sequence"] for row in rows_a])
        seqs_b = sorted([row["sequence"] for row in rows_b])

        # Each should have sequences 1-5 independently
        assert seqs_a == [1, 2, 3, 4, 5], f"call_id_a sequences: {seqs_a}"
        assert seqs_b == [1, 2, 3, 4, 5], f"call_id_b sequences: {seqs_b}"

    finally:
        # Cleanup
        async with db_pool.connection() as conn:
            await conn.execute("DELETE FROM conversation_events WHERE call_id = $1", call_id_a)
            await conn.execute("DELETE FROM conversation_calls WHERE call_id = $1", call_id_a)
            await conn.execute("DELETE FROM conversation_events WHERE call_id = $1", call_id_b)
            await conn.execute("DELETE FROM conversation_calls WHERE call_id = $1", call_id_b)
