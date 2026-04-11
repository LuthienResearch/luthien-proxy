"""Dataclasses for scheduled task configuration and observability.

Three related types live here:

* :class:`ScheduledTaskConfig` — the user-facing config a policy passes to
  ``Scheduler.schedule()``.
* :class:`RunningTask` — the scheduler-internal handle for a task that's
  actively running. Holds the ``asyncio.Task`` plus mutable bookkeeping.
* :class:`TaskStatus` — the read-only snapshot returned by the admin API.

``RunningTask`` holds the mutable counters (last_run_at, run_count, …) so
the scheduler can update them in place without rebuilding state every tick.
``TaskStatus`` is the immutable snapshot for external consumers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

TaskRunStatus = Literal["pending", "success", "error"]


@dataclass(frozen=True)
class ScheduledTaskConfig:
    """Declarative config for a periodic task.

    Attributes:
        name: Unique identifier for the task. Used as a dict key in the
            scheduler and surfaced in observability; must be unique within
            a single scheduler instance.
        interval: How long to wait between runs. Measured from the end of
            one run to the start of the next (not wall-clock ticks).
        callback: The zero-arg async callable to invoke each tick.
        jitter: Optional random offset added to each sleep, uniformly
            sampled from ``[-jitter, +jitter]``. Must be non-negative and
            strictly smaller than ``interval`` so the effective delay is
            always positive.
        run_immediately: If True, run the callback once at registration
            time before entering the sleep loop. Useful for "warm up the
            cache on policy load" patterns.
    """

    name: str
    interval: timedelta
    callback: Callable[[], Awaitable[None]]
    jitter: timedelta = field(default_factory=lambda: timedelta(0))
    run_immediately: bool = False

    def __post_init__(self) -> None:
        """Validate config at construction time so policies fail loudly."""
        if not self.name:
            raise ValueError("ScheduledTaskConfig.name must be non-empty")
        if self.interval <= timedelta(0):
            raise ValueError(f"ScheduledTaskConfig.interval must be positive, got {self.interval}")
        if self.jitter < timedelta(0):
            raise ValueError(f"ScheduledTaskConfig.jitter must be non-negative, got {self.jitter}")
        if self.jitter >= self.interval:
            raise ValueError(
                f"ScheduledTaskConfig.jitter ({self.jitter}) must be strictly less than "
                f"interval ({self.interval}) to guarantee positive delays"
            )


@dataclass
class RunningTask:
    """Scheduler-internal handle for an actively-running task.

    Holds mutable counters the scheduler updates after each tick. External
    consumers should never see this directly — use :meth:`Scheduler.task_status`
    which returns :class:`TaskStatus` snapshots.

    ``task`` is typed Optional because we construct the handle before
    minting the asyncio.Task (the loop body needs a reference to this
    object), then assign it immediately after. Once the scheduler has
    returned from ``schedule()``, ``task`` is guaranteed non-None.
    """

    config: ScheduledTaskConfig
    task: asyncio.Task[None] | None = None
    run_count: int = 0
    error_count: int = 0
    last_run_at: datetime | None = None
    last_run_status: TaskRunStatus = "pending"
    last_error: str | None = None
    next_run_at: datetime | None = None

    @property
    def name(self) -> str:
        """Shortcut for ``self.config.name``."""
        return self.config.name

    def snapshot(self) -> TaskStatus:
        """Return an immutable observability snapshot."""
        return TaskStatus(
            name=self.config.name,
            interval_seconds=self.config.interval.total_seconds(),
            jitter_seconds=self.config.jitter.total_seconds(),
            run_count=self.run_count,
            error_count=self.error_count,
            last_run_at=self.last_run_at.isoformat() if self.last_run_at else None,
            last_run_status=self.last_run_status,
            last_error=self.last_error,
            next_run_at=self.next_run_at.isoformat() if self.next_run_at else None,
        )


@dataclass(frozen=True)
class TaskStatus:
    """Immutable observability snapshot for a scheduled task.

    Returned by :meth:`Scheduler.task_status` and the admin API. All
    timestamps are ISO-8601 strings so the payload is JSON-serializable
    without custom encoders.
    """

    name: str
    interval_seconds: float
    jitter_seconds: float
    run_count: int
    error_count: int
    last_run_at: str | None
    last_run_status: TaskRunStatus
    last_error: str | None
    next_run_at: str | None


__all__ = [
    "ScheduledTaskConfig",
    "RunningTask",
    "TaskStatus",
    "TaskRunStatus",
]
