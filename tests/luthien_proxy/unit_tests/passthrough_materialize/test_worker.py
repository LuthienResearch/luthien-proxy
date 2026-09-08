from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import aiosqlite
import pytest

import luthien_proxy.passthrough_materialize.worker as worker_module
from luthien_proxy.passthrough_materialize.materialize_types import ReconcileStats
from luthien_proxy.passthrough_materialize.worker import PassthroughReconcileWorker
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations


@pytest.fixture
async def worker_pool() -> AsyncIterator[DatabasePool]:
    pool = DatabasePool("sqlite://:memory:")
    await check_migrations(pool)
    yield pool
    await pool.close()


async def test_reconcile_worker_runs_a_sweep_with_its_configured_limit(
    worker_pool: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    sweep_started = asyncio.Event()
    observed_limits: list[int] = []

    async def reconcile(pool: DatabasePool, *, limit: int) -> ReconcileStats:
        assert pool is worker_pool
        observed_limits.append(limit)
        sweep_started.set()
        return ReconcileStats(materialized=1)

    monkeypatch.setattr(worker_module, "reconcile_passthrough", reconcile)
    worker = PassthroughReconcileWorker(db_pool=worker_pool, limit=23, interval_seconds=60)

    # When
    worker.start()
    try:
        await asyncio.wait_for(sweep_started.wait(), timeout=1)
    finally:
        await worker.stop()

    # Then
    assert observed_limits == [23]


async def test_reconcile_worker_continues_after_a_database_sweep_error(
    worker_pool: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    original_sleep = asyncio.sleep
    second_sweep_started = asyncio.Event()
    sweep_count = 0

    async def reconcile(pool: DatabasePool, *, limit: int) -> ReconcileStats:
        nonlocal sweep_count
        assert pool is worker_pool
        assert limit == 23
        sweep_count += 1
        if sweep_count == 1:
            raise aiosqlite.OperationalError("transient database failure")
        second_sweep_started.set()
        return ReconcileStats()

    async def advance_without_delay(_seconds: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr(worker_module, "reconcile_passthrough", reconcile)
    monkeypatch.setattr(worker_module.asyncio, "sleep", advance_without_delay)
    worker = PassthroughReconcileWorker(db_pool=worker_pool, limit=23, interval_seconds=60)

    # When
    worker.start()
    try:
        await asyncio.wait_for(second_sweep_started.wait(), timeout=1)
    finally:
        await worker.stop()

    # Then
    assert sweep_count >= 2


async def test_reconcile_worker_stop_cancels_an_inflight_sweep(
    worker_pool: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    sweep_started = asyncio.Event()

    async def reconcile(pool: DatabasePool, *, limit: int) -> ReconcileStats:
        assert pool is worker_pool
        assert limit == 23
        sweep_started.set()
        await asyncio.Event().wait()
        return ReconcileStats()

    monkeypatch.setattr(worker_module, "reconcile_passthrough", reconcile)
    worker = PassthroughReconcileWorker(db_pool=worker_pool, limit=23, interval_seconds=60)
    worker.start()
    task = worker._task
    assert task is not None
    await asyncio.wait_for(sweep_started.wait(), timeout=1)

    # When
    await worker.stop()

    # Then
    assert task.cancelled()
    assert worker._task is None
