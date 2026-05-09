"""Background task that purges old conversation data from the database.

Runs on a configurable interval (default: daily). When an archiver is
configured, the purger drives an archive-then-delete-per-batch loop:

    1. fetch a batch of calls older than cutoff (outside any DB transaction)
    2. upload the batch to S3 (also outside any DB transaction)
    3. open a short transaction; DELETE WHERE call_id IN (this batch)
    4. advance the cursor; loop

This keeps memory bounded to one batch even on a million-row first-run
backfill, and decouples S3 latency from DB lock duration. If a batch's
upload fails, earlier batches are already archived and deleted, and the
unarchived rows remain for the next run to retry.

Cascading FK deletes handle conversation_events, policy_events, and
conversation_judge_decisions.

Follows the same start/stop pattern as TelemetrySender.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from luthien_proxy.retention.archiver import S3ConversationArchiver
    from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)

# Default: run once per day
DEFAULT_INTERVAL_SECONDS = 86_400
# Wait 60 s after startup before first purge to avoid startup contention
DEFAULT_INITIAL_DELAY_SECONDS = 60
# Max ids per DELETE statement. SQLite caps parameters at ~999 by default;
# Postgres has no practical limit but bounded statements are easier on the
# planner and keep individual transactions short.
_DELETE_CHUNK_SIZE = 500


def _log_task_exception(task: asyncio.Task[None]) -> None:
    """Log exceptions from fire-and-forget tasks to prevent silent failures."""
    if not task.cancelled() and (exc := task.exception()):
        logger.exception("Purger background task raised an unexpected exception", exc_info=exc)


class ConversationPurger:
    """Periodically purges conversation_calls older than retention_days.

    Args:
        db_pool: Database connection pool.
        retention_days: Delete rows older than this many days.
        archiver: Optional S3 archiver. When provided, calls are archived
            (outside any DB transaction) before deletion, one batch at a
            time. If a batch's upload fails, deletion is skipped for that
            batch (and the rest of the run is aborted).
        initial_delay_seconds: Seconds to wait after start() before first run.
        interval_seconds: Seconds between subsequent runs.
    """

    def __init__(
        self,
        *,
        db_pool: "DatabasePool",
        retention_days: int,
        archiver: "S3ConversationArchiver | None" = None,
        initial_delay_seconds: int = DEFAULT_INITIAL_DELAY_SECONDS,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        """Initialize purger with DB pool, retention policy, and optional archiver."""
        self._db_pool = db_pool
        self._retention_days = retention_days
        self._archiver = archiver
        self._initial_delay_seconds = initial_delay_seconds
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def _cutoff_datetime(self) -> datetime:
        """Return the cutoff: rows older than this will be purged."""
        return datetime.now(UTC) - timedelta(days=self._retention_days)

    async def _delete_by_call_ids(self, call_ids: list[str]) -> int:
        """Delete the named conversation_calls rows in chunks, return total deleted."""
        if not call_ids:
            return 0
        deleted = 0
        async with self._db_pool.connection() as conn:
            async with conn.transaction():
                for start in range(0, len(call_ids), _DELETE_CHUNK_SIZE):
                    chunk = call_ids[start : start + _DELETE_CHUNK_SIZE]
                    placeholders = ",".join(f"${i + 1}" for i in range(len(chunk)))
                    await conn.execute(
                        f"DELETE FROM conversation_calls WHERE call_id IN ({placeholders})",
                        *chunk,
                    )
                    deleted += len(chunk)
        return deleted

    async def _delete_by_cutoff(self, cutoff: datetime) -> int:
        """Delete all rows older than cutoff (no archive). Returns count.

        Used only when no archiver is configured. Postgres uses a CTE with
        DELETE ... RETURNING for an exact count; SQLite (which lacks the CTE
        form) does count-then-delete inside a single transaction.
        """
        async with self._db_pool.connection() as conn:
            async with conn.transaction():
                if self._db_pool.is_sqlite:
                    count_before = await conn.fetchval(
                        "SELECT COUNT(*) FROM conversation_calls WHERE created_at < $1",
                        cutoff,
                    )
                    await conn.execute(
                        "DELETE FROM conversation_calls WHERE created_at < $1",
                        cutoff,
                    )
                    return count_before if isinstance(count_before, int) else 0
                deleted = await conn.fetchval(
                    """
                    WITH deleted AS (
                        DELETE FROM conversation_calls
                        WHERE created_at < $1
                        RETURNING call_id
                    )
                    SELECT COUNT(*) FROM deleted
                    """,
                    cutoff,
                )
                return deleted if isinstance(deleted, int) else 0

    async def _archive_and_delete_per_batch(self, cutoff: datetime) -> int:
        """Archive one batch, delete it, advance cursor; loop until done.

        Memory and DB transaction footprint stay bounded to one batch, so
        a first-run backfill of millions of rows does not OOM the gateway
        or hold a long transaction.
        """
        assert self._archiver is not None
        archiver = self._archiver
        run_id = archiver.new_run_id()
        last_call_id: str | None = None
        batch_index = 0
        total_deleted = 0

        while True:
            try:
                async with self._db_pool.connection() as conn:
                    archived_ids, has_more = await archiver.archive_one_batch(
                        db_conn=conn,
                        cutoff=cutoff,
                        last_call_id=last_call_id,
                        run_id=run_id,
                        batch_index=batch_index,
                    )
            except Exception:
                logger.exception(
                    "Archive failed on batch %d (run=%s); stopping. %d batches archived+deleted earlier in this run.",
                    batch_index,
                    run_id,
                    batch_index,
                )
                return total_deleted

            if not archived_ids:
                break

            try:
                total_deleted += await self._delete_by_call_ids(archived_ids)
            except Exception:
                logger.exception(
                    "DELETE failed for archived batch %d (run=%s); stopping. "
                    "S3 has the archive; DB still has the rows. Next run will re-archive.",
                    batch_index,
                    run_id,
                )
                return total_deleted

            # Advance the cursor regardless of whether the batch was full —
            # if it wasn't, the next iteration will return zero rows and exit.
            last_call_id = archived_ids[-1]
            batch_index += 1
            if not has_more:
                break

        if total_deleted > 0:
            logger.info(
                "Archive run %s complete: %d records across %d batch(es)",
                run_id,
                total_deleted,
                batch_index,
            )
        return total_deleted

    async def purge_once(self) -> int:
        """Run a single purge cycle.

        With an archiver: drive an archive-then-delete-per-batch loop.
        Without: DELETE everything older than cutoff in one transaction.

        Returns:
            Number of conversation_calls rows deleted (0 on error or
            nothing to delete).
        """
        cutoff = self._cutoff_datetime()
        logger.info(
            "Running conversation purge: deleting calls older than %s (retention=%d days)",
            cutoff.isoformat(),
            self._retention_days,
        )

        try:
            if self._archiver is not None:
                count = await self._archive_and_delete_per_batch(cutoff)
            else:
                count = await self._delete_by_cutoff(cutoff)
        except Exception:
            logger.exception("Conversation purge failed")
            return 0

        if count > 0:
            logger.info("Purged %d conversation_calls (cutoff=%s)", count, cutoff.isoformat())
        else:
            logger.debug("No conversation_calls to purge (cutoff=%s)", cutoff.isoformat())
        return count

    async def _run_loop(self) -> None:
        """Periodic purge loop. Runs until cancelled."""
        await asyncio.sleep(self._initial_delay_seconds)
        while True:
            await self.purge_once()
            await asyncio.sleep(self._interval_seconds)

    def start(self) -> None:
        """Start the periodic purge loop as a background task."""
        if self._task is not None and not self._task.done():
            return
        logger.info(
            "Conversation retention enabled: retention_days=%d, interval=%ds, initial_delay=%ds",
            self._retention_days,
            self._interval_seconds,
            self._initial_delay_seconds,
        )
        self._task = asyncio.create_task(self._run_loop())
        self._task.add_done_callback(_log_task_exception)

    async def stop(self) -> None:
        """Cancel the purge loop and wait for it to finish."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
