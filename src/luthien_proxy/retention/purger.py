"""Background task that purges old conversation data from the database.

Runs on a configurable interval (default: daily). When ARCHIVE_S3_BUCKET is
set, archives rows to S3 before deleting. Cascading FK deletes handle
conversation_events, policy_events, and conversation_judge_decisions.

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


def _log_task_exception(task: asyncio.Task[None]) -> None:
    """Log exceptions from fire-and-forget tasks to prevent silent failures."""
    if not task.cancelled() and (exc := task.exception()):
        logger.exception("Purger background task raised an unexpected exception", exc_info=exc)


class ConversationPurger:
    """Periodically purges conversation_calls older than retention_days.

    Args:
        db_pool: Database connection pool.
        retention_days: Delete rows older than this many days.
        archiver: Optional S3 archiver. When provided, rows are archived
            before deletion. If archival fails, deletion is skipped.
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

    async def purge_once(self) -> int:
        """Run a single purge cycle.

        Returns:
            Number of conversation_calls rows deleted (0 on error or nothing to delete).
        """
        cutoff = self._cutoff_datetime()
        logger.info(
            "Running conversation purge: deleting calls older than %s (retention=%d days)",
            cutoff.isoformat(),
            self._retention_days,
        )

        try:
            async with self._db_pool.connection() as conn:
                # Archive before delete if archiver is configured
                if self._archiver is not None:
                    try:
                        await self._archiver.archive_calls(db_conn=conn, cutoff=cutoff)
                    except Exception:
                        logger.exception("S3 archival failed — skipping purge to avoid data loss")
                        return 0

                # Delete old calls; cascading FKs clean up child tables.
                # SQLite does not support DELETE...RETURNING inside a CTE, so we
                # use a two-step approach: fetch the count first, then delete.
                # Postgres uses the more efficient CTE form.
                if self._db_pool.is_sqlite:
                    count_before = await conn.fetchval(
                        "SELECT COUNT(*) FROM conversation_calls WHERE created_at < $1",
                        cutoff,
                    )
                    await conn.execute(
                        "DELETE FROM conversation_calls WHERE created_at < $1",
                        cutoff,
                    )
                    count = int(count_before) if isinstance(count_before, (int, float)) else 0
                else:
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
                    count = int(deleted) if isinstance(deleted, (int, float)) else 0
                if count > 0:
                    logger.info("Purged %d conversation_calls (cutoff=%s)", count, cutoff.isoformat())
                else:
                    logger.debug("No conversation_calls to purge (cutoff=%s)", cutoff.isoformat())
                return count

        except Exception:
            logger.exception("Conversation purge failed")
            return 0

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
