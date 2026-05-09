"""Unit tests for ConversationPurger.

Covers:
- Archive-then-delete-per-batch loop with the archiver providing one batch
  at a time. Memory and DB transactions stay bounded to one batch.
- Purger advances the cursor with the last archived call_id of each batch.
- A failed archive batch leaves earlier batches durably archived+deleted.
- A failed DELETE leaves S3 archived but the rows still in the DB (next
  run will re-archive — duplicates in S3, no data loss).
- No-archive path: delete-by-cutoff in one transaction.
- Lifecycle: start/stop, idempotent start, restart-after-stop, exception logging.
"""

from __future__ import annotations

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


def _make_archiver(
    *,
    batches: list[tuple[list[str], bool]],
    upload_side_effect: object | None = None,
) -> MagicMock:
    """Build a mock archiver whose split fetch_batch + upload_batch produce the given batches.

    Each batch is a tuple ``(call_ids, has_more)``. fetch_batch returns
    ``(body, call_ids, has_more)`` derived from the tuple; upload_batch is a
    plain AsyncMock the caller can attach a side_effect to via
    ``upload_side_effect`` (a single exception, or a list of values to
    return / raise per call).

    new_run_id returns a deterministic value for assertions.
    """
    archiver = MagicMock()
    archiver.new_run_id = MagicMock(return_value="testrun1")
    archiver.fetch_batch = AsyncMock(
        side_effect=[(b"<jsonl>", call_ids, has_more) for call_ids, has_more in batches]
    )
    if upload_side_effect is None:
        archiver.upload_batch = AsyncMock(return_value=None)
    elif isinstance(upload_side_effect, list):
        archiver.upload_batch = AsyncMock(side_effect=upload_side_effect)
    else:
        archiver.upload_batch = AsyncMock(side_effect=upload_side_effect)
    return archiver


# ── archive-then-delete loop semantics ────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_then_delete_per_batch_loops_until_no_more():
    pool, conn = _make_pool()
    archiver = _make_archiver(
        batches=[
            (["c1", "c2"], True),  # full batch -> keep going
            (["c3"], False),  # partial batch -> stop
        ]
    )

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == 3
    assert archiver.fetch_batch.call_count == 2
    assert archiver.upload_batch.call_count == 2
    # Cursor advances to last archived id of previous batch.
    second_fetch_kwargs = archiver.fetch_batch.call_args_list[1].kwargs
    assert second_fetch_kwargs["last_call_id"] == "c2"
    second_upload_kwargs = archiver.upload_batch.call_args_list[1].kwargs
    assert second_upload_kwargs["batch_index"] == 1


@pytest.mark.asyncio
async def test_archive_then_delete_stops_when_first_batch_empty():
    pool, conn = _make_pool()
    archiver = _make_archiver(batches=[([], False)])

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == 0
    archiver.fetch_batch.assert_called_once()
    archiver.upload_batch.assert_not_called()
    # No DELETE issued because there were no archived ids.
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_upload_failure_mid_run_preserves_earlier_batches():
    """First batch archives+deletes successfully; second batch's S3 upload fails."""
    pool, conn = _make_pool()
    archiver = _make_archiver(
        batches=[(["c1", "c2"], True), (["c3"], False)],
        upload_side_effect=[None, RuntimeError("S3 down")],
    )

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    # Batch 0's DELETE happened; batch 1 was fetched but failed to upload, so no DELETE.
    assert count == 2
    assert conn.execute.call_count == 1
    assert archiver.fetch_batch.call_count == 2
    assert archiver.upload_batch.call_count == 2


@pytest.mark.asyncio
async def test_fetch_failure_mid_run_preserves_earlier_batches():
    """First batch archives+deletes successfully; second batch fails to fetch from DB."""
    pool, conn = _make_pool()
    archiver = MagicMock()
    archiver.new_run_id = MagicMock(return_value="testrun1")
    archiver.fetch_batch = AsyncMock(
        side_effect=[(b"<jsonl>", ["c1", "c2"], True), RuntimeError("DB error")]
    )
    archiver.upload_batch = AsyncMock(return_value=None)

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == 2
    assert conn.execute.call_count == 1


@pytest.mark.asyncio
async def test_delete_failure_mid_run_stops_loop_archive_kept():
    """Archive succeeds; the DELETE for that batch fails; loop stops."""
    pool, conn = _make_pool()

    # Make the first DELETE fail.
    delete_call_count = {"n": 0}

    async def failing_execute(*args, **kwargs):
        delete_call_count["n"] += 1
        if delete_call_count["n"] == 1:
            raise RuntimeError("DELETE failed")
        return None

    conn.execute = AsyncMock(side_effect=failing_execute)
    archiver = _make_archiver(batches=[(["c1"], True), (["c2"], False)])

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == 0
    # Loop stopped after first DELETE failure — second batch never fetched.
    assert archiver.fetch_batch.call_count == 1


@pytest.mark.asyncio
async def test_db_connection_released_before_s3_upload():
    """Connection from phase-1 fetch must be released before phase-2 S3 upload starts.

    This is the regression guard for the medium-severity finding from review
    round 5 (#1): holding a pool slot across the S3 PUT pressures the pool
    when the gateway has unrelated traffic.
    """
    pool = MagicMock()
    pool.is_sqlite = False
    conn = _make_conn()

    enter_count = {"n": 0}
    exit_count = {"n": 0}
    s3_upload_count = {"n": 0}

    async def aenter(*_args, **_kwargs):
        enter_count["n"] += 1
        return conn

    async def aexit(*_args, **_kwargs):
        exit_count["n"] += 1
        return False

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=aenter)
    cm.__aexit__ = AsyncMock(side_effect=aexit)
    pool.connection = MagicMock(return_value=cm)

    async def upload_records_state(*args, **kwargs):
        # When upload is invoked, the only connection scope from phase-1
        # must already have exited (1 enter + 1 exit), and phase-3 has not
        # started yet (no second enter).
        s3_upload_count["n"] += 1
        assert enter_count["n"] == 1, "phase-1 conn must be entered before upload"
        assert exit_count["n"] == 1, "phase-1 conn must be released before S3 upload"
        return None

    archiver = _make_archiver(batches=[(["c1"], False)])
    archiver.upload_batch = AsyncMock(side_effect=upload_records_state)

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == 1
    assert s3_upload_count["n"] == 1
    # Two connection scopes total: phase-1 fetch + phase-3 DELETE.
    assert enter_count["n"] == 2
    assert exit_count["n"] == 2


@pytest.mark.asyncio
async def test_delete_uses_id_list_not_cutoff_predicate():
    pool, conn = _make_pool()
    archiver = _make_archiver(batches=[(["c1", "c2", "c3"], False)])

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    await purger.purge_once()

    delete_sql = conn.execute.call_args.args[0]
    assert "DELETE FROM conversation_calls WHERE call_id IN" in delete_sql
    assert "created_at" not in delete_sql


@pytest.mark.asyncio
async def test_delete_chunks_large_batches():
    """A single archived batch larger than _DELETE_CHUNK_SIZE issues multiple DELETEs."""
    from luthien_proxy.retention.purger import _DELETE_CHUNK_SIZE

    pool, conn = _make_pool()
    big_batch = [f"call-{i:04d}" for i in range(_DELETE_CHUNK_SIZE + 50)]
    archiver = _make_archiver(batches=[(big_batch, False)])

    purger = ConversationPurger(db_pool=pool, retention_days=30, archiver=archiver)
    count = await purger.purge_once()

    assert count == _DELETE_CHUNK_SIZE + 50
    assert conn.execute.call_count == 2  # ceil(550/500)


# ── no-archiver path ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_archiver_loops_in_bounded_batches():
    """No-archiver path mirrors the archiver path's per-batch bound.

    A first-run cleanup of millions of rows must not be one big DELETE.
    """
    pool, conn = _make_pool()
    # Two pages of fetched call_ids, then empty.
    fetch_responses = [
        [{"call_id": f"c{i}"} for i in range(500)],  # full page → keep going
        [{"call_id": "c500"}, {"call_id": "c501"}],  # partial page → stop
    ]
    conn.fetch = AsyncMock(side_effect=fetch_responses)

    purger = ConversationPurger(db_pool=pool, retention_days=30)
    count = await purger.purge_once()

    assert count == 502
    # Two SELECTs (one per page), and DELETEs in chunks (502 / 500 = 2 chunks).
    assert conn.fetch.call_count == 2
    assert conn.execute.call_count == 2
    # Cursor advances using last call_id of previous page.
    second_fetch_args = conn.fetch.call_args_list[1].args
    assert second_fetch_args[2] == "c499"


@pytest.mark.asyncio
async def test_no_archiver_empty_window_no_delete():
    pool, conn = _make_pool()
    conn.fetch = AsyncMock(return_value=[])

    purger = ConversationPurger(db_pool=pool, retention_days=30)
    count = await purger.purge_once()

    assert count == 0
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_no_archiver_db_error_returns_zero():
    pool, conn = _make_pool()
    conn.fetch = AsyncMock(side_effect=RuntimeError("DB lost"))

    purger = ConversationPurger(db_pool=pool, retention_days=30)
    count = await purger.purge_once()

    assert count == 0


# ── lifecycle ─────────────────────────────────────────────────────────────


def test_cutoff_calculation():
    pool = MagicMock()
    purger = ConversationPurger(db_pool=pool, retention_days=30)
    before = datetime.now(UTC)
    cutoff = purger._cutoff_datetime()
    after = datetime.now(UTC)
    assert before - timedelta(days=30) <= cutoff <= after - timedelta(days=30)


@pytest.mark.asyncio
async def test_start_stop_lifecycle():
    pool, _ = _make_pool()
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
    pool, _ = _make_pool()
    purger = ConversationPurger(
        db_pool=pool, retention_days=30, initial_delay_seconds=0, interval_seconds=9999
    )
    purger.start()
    first_task = purger._task
    purger.start()
    assert purger._task is first_task
    await purger.stop()


@pytest.mark.asyncio
async def test_stop_then_start_creates_new_task():
    pool, _ = _make_pool()
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
async def test_loop_exception_logged(caplog):
    pool, _ = _make_pool()
    purger = ConversationPurger(
        db_pool=pool, retention_days=30, initial_delay_seconds=0, interval_seconds=9999
    )

    async def failing() -> None:
        raise RuntimeError("boom")

    purger._run_loop = failing  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        purger.start()
        # Wait for the failing task to actually finish — done-callback fires synchronously after.
        assert purger._task is not None
        try:
            await purger._task
        except RuntimeError:
            pass

    purger._task = None  # Drain so stop() short-circuits.
    assert any("Purger background task raised an unexpected exception" in r.message for r in caplog.records)
