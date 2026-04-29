"""Unit tests for ConversationPurger.

Tests cover:
- purge_once: deletes rows older than retention_days, returns count
- purge_once: no-op when no old rows exist
- purge_once: handles DB errors gracefully (logs, does not raise)
- _run_loop: calls purge_once after initial delay, then periodically
- start/stop lifecycle
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.retention.purger import ConversationPurger


@pytest.fixture
def mock_db_pool():
    """Mock DatabasePool (Postgres dialect) with async execute and fetch methods."""
    pool = MagicMock()
    pool.is_sqlite = False
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="DELETE 5")
    conn.fetchval = AsyncMock(return_value=5)
    pool.connection = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.fixture
def mock_sqlite_pool():
    """Mock DatabasePool (SQLite dialect) — uses two-step count+delete."""
    pool = MagicMock()
    pool.is_sqlite = True
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=4)
    pool.connection = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.mark.asyncio
async def test_purge_once_deletes_old_rows(mock_db_pool):
    """purge_once should delete rows older than retention_days and return count."""
    pool, conn = mock_db_pool
    conn.fetchval = AsyncMock(return_value=7)

    purger = ConversationPurger(db_pool=pool, retention_days=30)
    count = await purger.purge_once()

    assert count == 7
    conn.fetchval.assert_called_once()
    # Verify the query references conversation_calls
    call_args = conn.fetchval.call_args
    assert "conversation_calls" in call_args[0][0]


@pytest.mark.asyncio
async def test_purge_once_returns_zero_when_nothing_to_delete(mock_db_pool):
    """purge_once returns 0 when no rows are old enough."""
    pool, conn = mock_db_pool
    conn.fetchval = AsyncMock(return_value=0)

    purger = ConversationPurger(db_pool=pool, retention_days=90)
    count = await purger.purge_once()

    assert count == 0


@pytest.mark.asyncio
async def test_purge_once_handles_db_error_gracefully(mock_db_pool):
    """purge_once should catch DB errors, log them, and return 0."""
    pool, conn = mock_db_pool
    conn.fetchval = AsyncMock(side_effect=Exception("DB connection lost"))

    purger = ConversationPurger(db_pool=pool, retention_days=30)
    # Should not raise
    count = await purger.purge_once()
    assert count == 0


@pytest.mark.asyncio
async def test_purge_once_with_archiver_calls_archive_before_delete(mock_db_pool):
    """When archiver is provided, purge_once archives before deleting."""
    pool, conn = mock_db_pool
    conn.fetchval = AsyncMock(return_value=3)

    mock_archiver = AsyncMock()
    mock_archiver.archive_calls = AsyncMock()

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=mock_archiver)
    count = await purger.purge_once()

    mock_archiver.archive_calls.assert_called_once()
    assert count == 3


@pytest.mark.asyncio
async def test_purge_once_skips_delete_if_archive_fails(mock_db_pool):
    """If archiver raises, purge_once should not delete and return 0."""
    pool, conn = mock_db_pool

    mock_archiver = AsyncMock()
    mock_archiver.archive_calls = AsyncMock(side_effect=Exception("S3 unavailable"))

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=mock_archiver)
    count = await purger.purge_once()

    # Should not have called delete
    conn.fetchval.assert_not_called()
    assert count == 0


@pytest.mark.asyncio
async def test_start_stop_lifecycle(mock_db_pool):
    """start() creates a background task; stop() cancels it cleanly."""
    pool, conn = mock_db_pool
    conn.fetchval = AsyncMock(return_value=0)

    purger = ConversationPurger(
        db_pool=pool,
        retention_days=30,
        initial_delay_seconds=0,
        interval_seconds=9999,  # Won't fire during test
    )

    task_ref = None

    purger.start()
    assert purger._task is not None
    assert not purger._task.done()
    task_ref = purger._task

    await purger.stop()
    # stop() sets _task to None after cancellation
    assert purger._task is None
    assert task_ref.done()


@pytest.mark.asyncio
async def test_run_loop_calls_purge_after_initial_delay(mock_db_pool):
    """_run_loop should call purge_once after initial_delay_seconds."""
    pool, conn = mock_db_pool
    conn.fetchval = AsyncMock(return_value=2)

    purger = ConversationPurger(
        db_pool=pool,
        retention_days=30,
        initial_delay_seconds=0,
        interval_seconds=9999,
    )

    purger.start()
    # Give the loop a chance to run the first iteration
    await asyncio.sleep(0.05)
    await purger.stop()

    # purge_once should have been called at least once
    assert conn.fetchval.call_count >= 1


@pytest.mark.asyncio
async def test_stop_is_idempotent(mock_db_pool):
    """Calling stop() multiple times should not raise."""
    pool, _ = mock_db_pool
    purger = ConversationPurger(db_pool=pool, retention_days=30)

    await purger.stop()  # Never started
    await purger.stop()  # Again — should be fine


def test_cutoff_date_calculation():
    """ConversationPurger computes cutoff as now() - retention_days."""
    pool = MagicMock()
    purger = ConversationPurger(db_pool=pool, retention_days=30)

    before = datetime.now(UTC)
    cutoff = purger._cutoff_datetime()
    after = datetime.now(UTC)

    assert before - timedelta(days=30) <= cutoff <= after - timedelta(days=30)


@pytest.mark.asyncio
async def test_purge_once_sqlite_uses_two_step_count_then_delete(mock_sqlite_pool):
    """SQLite path: fetchval for count, then execute for delete (no CTE RETURNING)."""
    pool, conn = mock_sqlite_pool
    conn.fetchval = AsyncMock(return_value=5)

    purger = ConversationPurger(db_pool=pool, retention_days=30)
    count = await purger.purge_once()

    assert count == 5
    conn.fetchval.assert_called_once()
    conn.execute.assert_called_once()
    count_sql = conn.fetchval.call_args[0][0]
    delete_sql = conn.execute.call_args[0][0]
    assert "COUNT(*)" in count_sql
    assert "DELETE" in delete_sql
    assert "WITH deleted" not in delete_sql


@pytest.mark.asyncio
async def test_purger_task_exception_logged(mock_db_pool, caplog):
    """When _run_loop raises, the exception callback logs it."""
    pool, conn = mock_db_pool

    purger = ConversationPurger(
        db_pool=pool,
        retention_days=30,
        initial_delay_seconds=0,
        interval_seconds=9999,
    )

    async def failing_run_loop():
        raise RuntimeError("boom")

    purger._run_loop = failing_run_loop

    with caplog.at_level(logging.ERROR):
        purger.start()
        await asyncio.sleep(0.05)

    try:
        await purger.stop()
    except RuntimeError:
        pass

    assert any("Purger background task raised an unexpected exception" in record.message for record in caplog.records)
