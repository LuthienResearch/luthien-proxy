"""Unit tests for ConversationPurger.

Covers:
- purge_once with archiver: archives outside transaction, then deletes only
  archived call_ids inside a short transaction.
- purge_once without archiver: deletes by cutoff in one transaction (postgres
  CTE form / sqlite count-then-delete form).
- Archive failure -> no DELETE, no count.
- Empty cutoff window -> no DELETE.
- Lifecycle: start/stop/start/stop, exception logging.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.retention.purger import ConversationPurger


def _make_conn() -> AsyncMock:
    """Build a fake DB connection with chainable async transaction context."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=0)
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


def _make_pool(*, is_sqlite: bool = False, conn: AsyncMock | None = None) -> tuple[MagicMock, AsyncMock]:
    pool = MagicMock()
    pool.is_sqlite = is_sqlite
    if conn is None:
        conn = _make_conn()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection = MagicMock(return_value=cm)
    return pool, conn


@pytest.mark.asyncio
async def test_purge_with_archiver_archives_then_deletes_only_archived_ids():
    pool, conn = _make_pool()
    archiver = AsyncMock()
    archiver.archive_calls = AsyncMock(return_value=["call-001", "call-002", "call-003"])

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == 3
    archiver.archive_calls.assert_called_once()
    # DELETE used the explicit id list, not a cutoff predicate
    delete_sql = conn.execute.call_args.args[0]
    assert "DELETE FROM conversation_calls WHERE call_id IN" in delete_sql
    assert "created_at" not in delete_sql


@pytest.mark.asyncio
async def test_archiver_called_outside_transaction():
    """Archive must run before the DELETE transaction is opened."""
    pool, conn = _make_pool()

    archive_seen_transaction_calls: list[int] = []

    async def fake_archive(*, db_conn, cutoff):
        archive_seen_transaction_calls.append(conn.transaction.call_count)
        return ["call-001"]

    archiver = MagicMock()
    archiver.archive_calls = fake_archive

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    await purger.purge_once()

    assert archive_seen_transaction_calls == [0]
    # By the end, exactly one DELETE transaction was opened.
    assert conn.transaction.call_count == 1


@pytest.mark.asyncio
async def test_archive_failure_skips_delete():
    pool, conn = _make_pool()
    archiver = AsyncMock()
    archiver.archive_calls = AsyncMock(side_effect=Exception("S3 down"))

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == 0
    conn.execute.assert_not_called()
    conn.transaction.assert_not_called()


@pytest.mark.asyncio
async def test_archive_returns_no_ids_skips_delete():
    pool, conn = _make_pool()
    archiver = AsyncMock()
    archiver.archive_calls = AsyncMock(return_value=[])

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == 0
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_purge_without_archiver_postgres_uses_cte():
    pool, conn = _make_pool(is_sqlite=False)
    conn.fetchval = AsyncMock(return_value=7)

    purger = ConversationPurger(db_pool=pool, retention_days=30)
    count = await purger.purge_once()

    assert count == 7
    fetch_sql = conn.fetchval.call_args.args[0]
    assert "WITH deleted" in fetch_sql
    assert "RETURNING call_id" in fetch_sql


@pytest.mark.asyncio
async def test_purge_without_archiver_sqlite_uses_count_then_delete():
    pool, conn = _make_pool(is_sqlite=True)
    conn.fetchval = AsyncMock(return_value=5)
    conn.execute = AsyncMock(return_value=None)

    purger = ConversationPurger(db_pool=pool, retention_days=30)
    count = await purger.purge_once()

    assert count == 5
    count_sql = conn.fetchval.call_args.args[0]
    delete_sql = conn.execute.call_args.args[0]
    assert "COUNT(*)" in count_sql
    assert "DELETE" in delete_sql
    assert "WITH" not in delete_sql


@pytest.mark.asyncio
async def test_purge_chunks_large_id_lists():
    """A list larger than _DELETE_CHUNK_SIZE is split across multiple DELETE statements."""
    from luthien_proxy.retention.purger import _DELETE_CHUNK_SIZE

    pool, conn = _make_pool()
    ids = [f"call-{i:04d}" for i in range(_DELETE_CHUNK_SIZE + 50)]
    archiver = AsyncMock()
    archiver.archive_calls = AsyncMock(return_value=ids)

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == _DELETE_CHUNK_SIZE + 50
    assert conn.execute.call_count == 2  # ceil(550 / 500)


@pytest.mark.asyncio
async def test_purge_handles_db_error():
    pool, conn = _make_pool()
    conn.fetchval = AsyncMock(side_effect=RuntimeError("DB lost"))

    purger = ConversationPurger(db_pool=pool, retention_days=30)
    count = await purger.purge_once()

    assert count == 0


def test_cutoff_calculation():
    pool = MagicMock()
    purger = ConversationPurger(db_pool=pool, retention_days=30)
    before = datetime.now(UTC)
    cutoff = purger._cutoff_datetime()
    after = datetime.now(UTC)
    assert before - timedelta(days=30) <= cutoff <= after - timedelta(days=30)


@pytest.mark.asyncio
async def test_start_stop_lifecycle():
    pool, conn = _make_pool()
    purger = ConversationPurger(
        db_pool=pool, retention_days=30, initial_delay_seconds=0, interval_seconds=9999
    )
    purger.start()
    task_ref = purger._task
    assert task_ref is not None and not task_ref.done()
    await purger.stop()
    assert purger._task is None
    assert task_ref.done()


@pytest.mark.asyncio
async def test_start_is_idempotent():
    pool, conn = _make_pool()
    purger = ConversationPurger(
        db_pool=pool, retention_days=30, initial_delay_seconds=0, interval_seconds=9999
    )
    purger.start()
    first_task = purger._task
    purger.start()  # should not replace
    assert purger._task is first_task
    await purger.stop()


@pytest.mark.asyncio
async def test_stop_then_start_creates_new_task():
    pool, conn = _make_pool()
    purger = ConversationPurger(
        db_pool=pool, retention_days=30, initial_delay_seconds=0, interval_seconds=9999
    )
    purger.start()
    first_task = purger._task
    await purger.stop()
    purger.start()
    assert purger._task is not first_task
    await purger.stop()


@pytest.mark.asyncio
async def test_stop_idempotent_when_never_started():
    pool, _ = _make_pool()
    purger = ConversationPurger(db_pool=pool, retention_days=30)
    await purger.stop()
    await purger.stop()


@pytest.mark.asyncio
async def test_loop_exception_is_logged(caplog):
    pool, _ = _make_pool()
    purger = ConversationPurger(
        db_pool=pool, retention_days=30, initial_delay_seconds=0, interval_seconds=9999
    )

    async def failing() -> None:
        raise RuntimeError("boom")

    purger._run_loop = failing  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        purger.start()
        await asyncio.sleep(0.05)

    try:
        await purger.stop()
    except RuntimeError:
        pass

    assert any("Purger background task raised an unexpected exception" in r.message for r in caplog.records)
