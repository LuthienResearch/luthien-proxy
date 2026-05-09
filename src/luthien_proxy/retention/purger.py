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

Index strategy: the existing ``idx_conversation_calls_created`` on
``conversation_calls(created_at)`` (from migration 003) is the index
this PR relies on. The query is
``WHERE created_at < $1 [AND call_id > $2] ORDER BY call_id LIMIT $3`` —
the planner range-scans matching rows via the existing index and sorts
the small result set by call_id. At the bounded ``batch_size=100``
default this is a tiny sort and runs at most every ``interval_seconds``
(daily by default), so adding a covering index isn't worth the
write-amplification on the gateway hot path.

A composite ``(created_at, call_id)`` would offer some benefit on a
first-run backfill where most rows match the cutoff (it lets the
planner pick a better plan for the cursor predicate), but on the
steady-state workload where only one day's worth of rows match per
run the savings are negligible against the hot-path cost. If first-run
backfill performance becomes a concern for a specific deployment,
adding the composite is a follow-up migration, not a structural
redesign.

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
# Postgres has no practical limit. All chunks for one batch run inside a
# single transaction — chunking is for parameter-count safety, not for
# transaction sizing.
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

    async def _fetch_call_ids_batch(
        self,
        conn: object,
        cutoff: datetime,
        last_call_id: str | None,
        batch_size: int,
    ) -> list[str]:
        """Fetch one batch of call_ids matching the cutoff, paginated by call_id."""
        if last_call_id is None:
            rows = await conn.fetch(  # type: ignore[attr-defined]
                "SELECT call_id FROM conversation_calls"
                " WHERE created_at < $1 ORDER BY call_id LIMIT $2",
                cutoff,
                batch_size,
            )
        else:
            rows = await conn.fetch(  # type: ignore[attr-defined]
                "SELECT call_id FROM conversation_calls"
                " WHERE created_at < $1 AND call_id > $2 ORDER BY call_id LIMIT $3",
                cutoff,
                last_call_id,
                batch_size,
            )
        return [row["call_id"] for row in rows]

    async def _delete_by_cutoff(self, cutoff: datetime) -> int:
        """Delete rows older than cutoff (no archive) in bounded batches.

        Mirrors the archiver path's per-batch bound on memory and lock
        duration: no single transaction holds DELETE locks across the
        entire backlog. A first-run cleanup of millions of rows runs as
        many small transactions, each touching at most _DELETE_CHUNK_SIZE
        rows from conversation_calls plus the cascading child rows.

        Works uniformly on Postgres and SQLite — no dialect branch.
        """
        total = 0
        last_call_id: str | None = None
        while True:
            async with self._db_pool.connection() as conn:
                call_ids = await self._fetch_call_ids_batch(
                    conn, cutoff, last_call_id, _DELETE_CHUNK_SIZE
                )
            if not call_ids:
                break
            total += await self._delete_by_call_ids(call_ids)
            last_call_id = call_ids[-1]
            if len(call_ids) < _DELETE_CHUNK_SIZE:
                break
        return total

    async def _archive_and_delete_per_batch(self, cutoff: datetime) -> int:
        """Archive one batch, delete it, advance cursor; loop until done.

        Each iteration follows three phases with the DB connection only
        held for phases 1 and 3:

          1. fetch the batch + child rows from the DB; release the conn
          2. PUT the JSONL to S3 (no DB held — S3 latency doesn't pin a
             pool slot)
          3. acquire a fresh conn for the short DELETE transaction

        Memory and per-statement DB load stay bounded to one batch, so a
        first-run backfill of millions of rows doesn't OOM the gateway or
        hold long transactions.
        """
        # Plain `if archiver is None: raise`: assertions are stripped under
        # `python -O` so an `assert` here would crash less informatively if
        # this method were ever called on a no-archiver purger.
        if self._archiver is None:
            raise RuntimeError("_archive_and_delete_per_batch called without an archiver")
        archiver = self._archiver
        run_id = archiver.new_run_id()
        last_call_id: str | None = None
        batch_index = 0
        total_deleted = 0

        while True:
            # Phase 1: fetch + serialize. Connection released at end of `with`.
            try:
                async with self._db_pool.connection() as conn:
                    body, archived_ids, has_more = await archiver.fetch_batch(
                        db_conn=conn,
                        cutoff=cutoff,
                        last_call_id=last_call_id,
                    )
            except Exception:
                logger.exception(
                    "Fetch failed on batch %d (run=%s); stopping. %d batch(es) succeeded earlier in this run.",
                    batch_index,
                    run_id,
                    batch_index,
                )
                return total_deleted

            if not archived_ids:
                break

            # Phase 2: S3 upload, no DB held.
            try:
                await archiver.upload_batch(
                    body=body,
                    cutoff=cutoff,
                    run_id=run_id,
                    batch_index=batch_index,
                    record_count=len(archived_ids),
                )
            except Exception:
                logger.exception(
                    "Archive upload failed on batch %d (run=%s); stopping. %d batch(es) succeeded earlier in this run.",
                    batch_index,
                    run_id,
                    batch_index,
                )
                return total_deleted

            # Phase 3: DELETE in a short transaction with a fresh conn.
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
        """Periodic purge loop. Runs until cancelled.

        ``purge_once`` swallows its own exceptions and returns 0, so this
        loop should never see one. The defensive try/except here is a
        belt-and-suspenders guard: a future bug that lets an exception
        escape ``purge_once`` would otherwise kill the daily task with no
        recovery short of a gateway restart.
        """
        await asyncio.sleep(self._initial_delay_seconds)
        while True:
            try:
                await self.purge_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("purge_once raised — continuing loop")
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
