"""Periodic background worker for passthrough reconciliation."""

from __future__ import annotations

import asyncio
import logging

import aiosqlite
import asyncpg

from luthien_proxy.passthrough_materialize.materialize_types import ReconcileStats
from luthien_proxy.passthrough_materialize.reconcile import reconcile_passthrough
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and (error := task.exception()):
        logger.exception("Passthrough reconciliation worker raised unexpectedly", exc_info=error)


def _sweep_selected_nothing(stats: ReconcileStats) -> bool:
    """Return whether a sweep's eligibility query matched zero transactions.

    Distinct from "did nothing succeed": a sweep that only hit failures
    (materialized=0) but still selected rows must keep running so it can
    dead-letter permanent ones and retry transient ones. Only an eligibility
    query that returned no rows at all means the backlog is actually drained.
    """
    return (
        stats.materialized == 0
        and stats.already_materialized == 0
        and stats.skipped_ineligible == 0
        and stats.failed == 0
    )


class PassthroughReconcileWorker:
    """Run bounded passthrough reconciliation sweeps until the backlog drains or shutdown.

    PASSTHROUGH_MATERIALIZE_BACKFILL_ENABLED is documented as a one-shot backfill
    for historical request_logs, not a perpetual poller. Once a sweep selects zero
    eligible transactions -- meaning nothing is left to materialize, retry, or
    dead-letter -- the loop exits instead of continuing to sweep an empty backlog
    every interval_seconds forever.
    """

    def __init__(self, *, db_pool: DatabasePool, limit: int, interval_seconds: int) -> None:
        """Initialize the database-backed worker and its cadence."""
        self._db_pool = db_pool
        self._limit = limit
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def _run_loop(self) -> None:
        while True:
            try:
                stats = await reconcile_passthrough(self._db_pool, limit=self._limit)
            except (aiosqlite.Error, asyncpg.PostgresError):
                logger.exception("Passthrough reconciliation sweep failed; retrying next interval")
            else:
                self._log_stats(stats)
                if _sweep_selected_nothing(stats):
                    logger.info("Passthrough reconciliation backlog drained; one-shot backfill worker stopping.")
                    return
            await asyncio.sleep(self._interval_seconds)

    def _log_stats(self, stats: ReconcileStats) -> None:
        logger.info(
            "Passthrough reconciliation sweep complete: materialized=%d already_materialized=%d "
            "skipped_ineligible=%d failed=%d",
            stats.materialized,
            stats.already_materialized,
            stats.skipped_ineligible,
            stats.failed,
        )

    def start(self) -> None:
        """Start the reconciliation task when one is not already active."""
        if self._task is not None and not self._task.done():
            return
        logger.info(
            "Passthrough reconciliation worker enabled: limit=%d interval=%ds",
            self._limit,
            self._interval_seconds,
        )
        self._task = asyncio.create_task(self._run_loop())
        self._task.add_done_callback(_log_task_exception)

    async def stop(self) -> None:
        """Cancel and drain the reconciliation task during shutdown."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None


__all__ = ["PassthroughReconcileWorker"]
