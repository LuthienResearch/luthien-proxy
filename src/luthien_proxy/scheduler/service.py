"""Async scheduler that owns and supervises policy background tasks.

The scheduler is a thin supervisor around :func:`asyncio.create_task`:

* :meth:`Scheduler.schedule` wraps a :class:`ScheduledTaskConfig` in a
  supervised asyncio task and registers it under ``config.name``.
* Each supervised task runs a simple loop: optionally run once immediately,
  then ``sleep(interval + jitter)``, run the callback, record the result,
  repeat. Exceptions in the callback are caught and logged; one bad tick
  never kills the task.
* :meth:`Scheduler.cancel` / :meth:`Scheduler.cancel_all` cancel the
  underlying task and wait for clean shutdown. They're idempotent and
  safe to call from the policy reload path.

The scheduler has no knowledge of policies — it just runs callables. The
policy lifecycle integration (calling ``register_scheduled_tasks`` on
load and ``cancel_all`` on reload/shutdown) lives in
:mod:`luthien_proxy.policy_manager`.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from luthien_proxy.scheduler.types import RunningTask, ScheduledTaskConfig, TaskStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Timezone-aware UTC "now" so ISO serialization is unambiguous."""
    return datetime.now(timezone.utc)


class Scheduler:
    """Supervises periodic async tasks on the gateway event loop.

    Not thread-safe: all public methods must be called from the asyncio
    loop that owns the scheduler. That matches the gateway's single-loop
    model (FastAPI lifespan → scheduler owned by PolicyManager).

    Task names must be unique within a scheduler instance. Attempting to
    schedule a task whose name is already registered raises ``ValueError``;
    cancel the old one first or pick a new name.
    """

    def __init__(self) -> None:
        """Construct an empty scheduler."""
        self._tasks: dict[str, RunningTask] = {}

    def schedule(self, config: ScheduledTaskConfig) -> RunningTask:
        """Register and start a supervised task.

        Args:
            config: Declarative task config; see :class:`ScheduledTaskConfig`.

        Returns:
            The :class:`RunningTask` handle (mostly for tests; production
            callers should treat the scheduler itself as the handle).

        Raises:
            ValueError: If a task with the same name is already scheduled.
        """
        if config.name in self._tasks:
            raise ValueError(f"Scheduled task '{config.name}' is already registered")

        # Build the bookkeeping handle first (the loop body needs a
        # reference so it can update counters after each tick) and then
        # mint the asyncio.Task and assign it back.
        running = RunningTask(
            config=config,
            next_run_at=_utcnow() if config.run_immediately else _utcnow() + config.interval,
        )
        running.task = asyncio.create_task(
            self._run_forever(running),
            name=f"scheduler:{config.name}",
        )
        self._tasks[config.name] = running
        logger.info(
            "Scheduled task '%s' (interval=%ss, jitter=%ss, run_immediately=%s)",
            config.name,
            config.interval.total_seconds(),
            config.jitter.total_seconds(),
            config.run_immediately,
        )
        return running

    async def cancel(self, name: str) -> bool:
        """Cancel a scheduled task by name.

        Args:
            name: The task name passed to :meth:`schedule`.

        Returns:
            True if a task with that name was cancelled, False if no task
            with that name was registered. Idempotent: calling twice on
            the same name returns False on the second call.
        """
        running = self._tasks.pop(name, None)
        if running is None:
            return False
        await self._cancel_task(running)
        return True

    async def cancel_all(self) -> None:
        """Cancel every registered task and wait for clean shutdown.

        Used by the policy reload path and gateway shutdown. Safe to call
        on an empty scheduler — it's a no-op in that case. After the call
        the scheduler is empty and can accept new schedules.
        """
        if not self._tasks:
            return
        to_cancel = list(self._tasks.values())
        self._tasks.clear()
        await asyncio.gather(
            *(self._cancel_task(running) for running in to_cancel),
            return_exceptions=True,
        )

    def task_status(self) -> list[TaskStatus]:
        """Return observability snapshots for every registered task.

        Snapshots are stable dicts safe to serialize. Ordering is
        insertion order (i.e. registration order), which happens to match
        the order a policy registers tasks in ``register_scheduled_tasks``.
        """
        return [running.snapshot() for running in self._tasks.values()]

    def has_task(self, name: str) -> bool:
        """Check whether a task with the given name is currently scheduled."""
        return name in self._tasks

    def __len__(self) -> int:
        """Return the number of currently-registered tasks."""
        return len(self._tasks)

    # =========================================================================
    # Internals
    # =========================================================================

    async def _run_forever(self, running: RunningTask) -> None:
        """Loop body for a supervised task.

        Runs until the asyncio task is cancelled. Callback exceptions are
        caught per-iteration so one bad tick can never take the task down;
        the scheduler logs the error, records it on the task status, and
        continues with the next sleep cycle.
        """
        config = running.config
        try:
            if config.run_immediately:
                await self._run_once(running)

            while True:
                delay = self._compute_delay(config)
                running.next_run_at = _utcnow() + timedelta(seconds=delay)
                await asyncio.sleep(delay)
                await self._run_once(running)
        except asyncio.CancelledError:
            logger.debug("Scheduled task '%s' cancelled", config.name)
            raise

    async def _run_once(self, running: RunningTask) -> None:
        """Invoke the callback once, updating counters and status."""
        config = running.config
        running.last_run_at = _utcnow()
        try:
            await config.callback()
        except asyncio.CancelledError:
            # Cancellation during the callback propagates; bookkeeping is
            # left consistent (pending → whatever state it was in) and
            # the outer loop re-raises.
            raise
        except Exception as exc:
            running.error_count += 1
            running.last_run_status = "error"
            running.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "Scheduled task '%s' raised %s (error #%d)",
                config.name,
                type(exc).__name__,
                running.error_count,
            )
        else:
            running.last_run_status = "success"
            running.last_error = None
        finally:
            running.run_count += 1

    @staticmethod
    def _compute_delay(config: ScheduledTaskConfig) -> float:
        """Compute the next sleep duration in seconds, applying jitter."""
        interval_s = config.interval.total_seconds()
        jitter_s = config.jitter.total_seconds()
        if jitter_s <= 0:
            return interval_s
        offset = random.uniform(-jitter_s, jitter_s)
        # __post_init__ guarantees jitter < interval, so this is positive.
        return interval_s + offset

    @staticmethod
    async def _cancel_task(running: RunningTask) -> None:
        """Cancel an asyncio task and swallow the expected CancelledError."""
        task = running.task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # A task that died with an uncaught exception before cancel
            # reached it is not a scheduler bug, but we still want to know.
            logger.warning(
                "Scheduled task '%s' exited with unexpected error during cancel: %r",
                running.name,
                exc,
            )


__all__ = ["Scheduler"]
