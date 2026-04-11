"""Unit tests for the gateway-wide policy scheduler."""

from __future__ import annotations

import asyncio
import random
from datetime import timedelta

import pytest

from luthien_proxy.scheduler import (
    RunningTask,
    ScheduledTaskConfig,
    Scheduler,
    TaskStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class CallRecorder:
    """Async-callable that records invocation times and optionally raises."""

    def __init__(self, *, raise_on: set[int] | None = None) -> None:
        self.calls: list[float] = []
        self._event = asyncio.Event()
        self._target: int | None = None
        self._raise_on = raise_on or set()

    async def __call__(self) -> None:
        # Timestamps are captured relative to the loop clock so tests can
        # assert spacing between consecutive invocations.
        self.calls.append(asyncio.get_event_loop().time())
        n = len(self.calls)
        if n in self._raise_on:
            raise RuntimeError(f"callback failure #{n}")
        if self._target is not None and n >= self._target:
            self._event.set()

    async def wait_for(self, count: int, timeout: float) -> None:
        """Block until the callback has been invoked at least ``count`` times."""
        self._target = count
        if len(self.calls) >= count:
            return
        self._event.clear()
        await asyncio.wait_for(self._event.wait(), timeout=timeout)


# ---------------------------------------------------------------------------
# ScheduledTaskConfig validation
# ---------------------------------------------------------------------------


class TestScheduledTaskConfig:
    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="non-empty"):
            ScheduledTaskConfig(name="", interval=timedelta(seconds=1), callback=_noop)

    def test_rejects_nonpositive_interval(self):
        with pytest.raises(ValueError, match="positive"):
            ScheduledTaskConfig(name="t", interval=timedelta(seconds=0), callback=_noop)
        with pytest.raises(ValueError, match="positive"):
            ScheduledTaskConfig(name="t", interval=timedelta(seconds=-1), callback=_noop)

    def test_rejects_negative_jitter(self):
        with pytest.raises(ValueError, match="non-negative"):
            ScheduledTaskConfig(
                name="t",
                interval=timedelta(seconds=1),
                callback=_noop,
                jitter=timedelta(seconds=-0.1),
            )

    def test_rejects_jitter_ge_interval(self):
        with pytest.raises(ValueError, match="strictly less than"):
            ScheduledTaskConfig(
                name="t",
                interval=timedelta(seconds=1),
                callback=_noop,
                jitter=timedelta(seconds=1),
            )


async def _noop() -> None:
    return None


# ---------------------------------------------------------------------------
# Scheduler behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestScheduler:
    async def test_schedules_and_reports_task(self):
        scheduler = Scheduler()
        cb = CallRecorder()
        scheduler.schedule(
            ScheduledTaskConfig(
                name="t1",
                interval=timedelta(milliseconds=20),
                callback=cb,
                run_immediately=True,
            )
        )
        assert scheduler.has_task("t1")
        assert len(scheduler) == 1

        try:
            await cb.wait_for(1, timeout=1.0)
            assert cb.calls, "expected at least one invocation"
        finally:
            await scheduler.cancel_all()

        assert len(scheduler) == 0

    async def test_run_immediately_invokes_before_first_sleep(self):
        scheduler = Scheduler()
        cb = CallRecorder()
        scheduler.schedule(
            ScheduledTaskConfig(
                name="t",
                interval=timedelta(seconds=60),  # long interval so the
                callback=cb,  # first call only comes
                run_immediately=True,  # from run_immediately
            )
        )
        try:
            await cb.wait_for(1, timeout=1.0)
            assert len(cb.calls) == 1
        finally:
            await scheduler.cancel_all()

    async def test_repeats_on_interval(self):
        scheduler = Scheduler()
        cb = CallRecorder()
        interval_s = 0.03
        scheduler.schedule(
            ScheduledTaskConfig(
                name="t",
                interval=timedelta(seconds=interval_s),
                callback=cb,
            )
        )
        try:
            await cb.wait_for(3, timeout=1.0)
            assert len(cb.calls) >= 3
            # Spacing between consecutive calls should be at least interval;
            # allow a generous slop for scheduler jitter.
            spacings = [cb.calls[i + 1] - cb.calls[i] for i in range(len(cb.calls) - 1)]
            assert all(s >= interval_s * 0.8 for s in spacings), spacings
        finally:
            await scheduler.cancel_all()

    async def test_callback_exception_does_not_kill_task(self):
        scheduler = Scheduler()
        # Raise on the first call; subsequent calls should still happen.
        cb = CallRecorder(raise_on={1})
        scheduler.schedule(
            ScheduledTaskConfig(
                name="flaky",
                interval=timedelta(milliseconds=20),
                callback=cb,
                run_immediately=True,
            )
        )
        try:
            await cb.wait_for(3, timeout=2.0)
            status = next(s for s in scheduler.task_status() if s.name == "flaky")
            assert status.error_count >= 1
            assert status.run_count >= 3
            # After the initial failure, subsequent successful runs clear
            # the last_error and flip status back to success.
            assert status.last_run_status == "success"
            assert status.last_error is None
        finally:
            await scheduler.cancel_all()

    async def test_cancel_stops_single_task(self):
        scheduler = Scheduler()
        cb = CallRecorder()
        scheduler.schedule(
            ScheduledTaskConfig(
                name="t",
                interval=timedelta(milliseconds=10),
                callback=cb,
                run_immediately=True,
            )
        )
        await cb.wait_for(1, timeout=1.0)
        cancelled = await scheduler.cancel("t")
        assert cancelled is True
        assert not scheduler.has_task("t")
        # A second cancel is idempotent.
        assert (await scheduler.cancel("t")) is False

        # After cancellation, the callback should stop accumulating new
        # invocations within a short window.
        snapshot_count = len(cb.calls)
        await asyncio.sleep(0.05)
        assert len(cb.calls) == snapshot_count

    async def test_cancel_unknown_returns_false(self):
        scheduler = Scheduler()
        assert (await scheduler.cancel("does-not-exist")) is False

    async def test_cancel_all_clears_scheduler(self):
        scheduler = Scheduler()
        cb1 = CallRecorder()
        cb2 = CallRecorder()
        scheduler.schedule(ScheduledTaskConfig(name="a", interval=timedelta(milliseconds=10), callback=cb1))
        scheduler.schedule(ScheduledTaskConfig(name="b", interval=timedelta(milliseconds=10), callback=cb2))
        assert len(scheduler) == 2
        await scheduler.cancel_all()
        assert len(scheduler) == 0
        # Safe to call on empty scheduler.
        await scheduler.cancel_all()

    async def test_duplicate_name_raises(self):
        scheduler = Scheduler()
        scheduler.schedule(ScheduledTaskConfig(name="dup", interval=timedelta(seconds=1), callback=_noop))
        with pytest.raises(ValueError, match="already registered"):
            scheduler.schedule(ScheduledTaskConfig(name="dup", interval=timedelta(seconds=1), callback=_noop))
        await scheduler.cancel_all()

    async def test_task_status_is_serializable(self):
        scheduler = Scheduler()
        cb = CallRecorder()
        scheduler.schedule(
            ScheduledTaskConfig(
                name="serial",
                interval=timedelta(milliseconds=20),
                callback=cb,
                run_immediately=True,
            )
        )
        try:
            await cb.wait_for(1, timeout=1.0)
            statuses = scheduler.task_status()
            assert len(statuses) == 1
            s = statuses[0]
            assert isinstance(s, TaskStatus)
            assert s.name == "serial"
            assert s.run_count >= 1
            assert s.last_run_at is not None
            # ISO-8601 strings should round-trip through fromisoformat.
            from datetime import datetime

            datetime.fromisoformat(s.last_run_at)
        finally:
            await scheduler.cancel_all()

    async def test_schedule_returns_running_task(self):
        scheduler = Scheduler()
        running = scheduler.schedule(ScheduledTaskConfig(name="rt", interval=timedelta(seconds=1), callback=_noop))
        assert isinstance(running, RunningTask)
        assert running.name == "rt"
        assert running.task is not None
        await scheduler.cancel_all()


# ---------------------------------------------------------------------------
# Jitter distribution
# ---------------------------------------------------------------------------


class TestJitter:
    def test_compute_delay_respects_bounds(self):
        config = ScheduledTaskConfig(
            name="j",
            interval=timedelta(seconds=1),
            callback=_noop,
            jitter=timedelta(milliseconds=200),
        )
        rng = random.Random(12345)
        # Patch the module-level random.uniform indirectly by seeding.
        random.seed(rng.random())
        for _ in range(500):
            delay = Scheduler._compute_delay(config)
            assert 0.8 <= delay <= 1.2

    def test_compute_delay_zero_jitter(self):
        config = ScheduledTaskConfig(
            name="j",
            interval=timedelta(seconds=2),
            callback=_noop,
        )
        for _ in range(10):
            assert Scheduler._compute_delay(config) == pytest.approx(2.0)
