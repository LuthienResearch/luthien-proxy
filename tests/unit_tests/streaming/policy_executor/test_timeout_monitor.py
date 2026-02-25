# ABOUTME: Unit tests for TimeoutMonitor
# ABOUTME: Tests deadline-based timeout tracking and keepalive mechanism

"""Tests for TimeoutMonitor."""

import asyncio
import time

import pytest

from luthien_proxy.streaming.policy_executor.timeout_monitor import PolicyTimeoutError, TimeoutMonitor


class TestTimeoutMonitor:
    """Tests for TimeoutMonitor deadline-based timeout tracking."""

    def test_initialization_with_timeout(self):
        """TimeoutMonitor initializes with timeout and sets initial deadline."""
        monitor = TimeoutMonitor(timeout_seconds=5.0)

        assert monitor.timeout_seconds == 5.0
        # Deadline should be approximately 5 seconds in the future
        time_until = monitor.time_until_timeout()
        assert 4.9 < time_until <= 5.0

    def test_initialization_without_timeout(self):
        """TimeoutMonitor can be initialized without timeout (disabled)."""
        monitor = TimeoutMonitor(timeout_seconds=None)

        assert monitor.timeout_seconds is None
        # Deadline should be infinity when disabled
        assert monitor.time_until_timeout() == float("inf")

    def test_time_until_timeout_decreases(self):
        """time_until_timeout() decreases as time passes."""
        monitor = TimeoutMonitor(timeout_seconds=10.0)

        time1 = monitor.time_until_timeout()
        time.sleep(0.15)
        time2 = monitor.time_until_timeout()

        # Deadline should be getting closer
        assert time2 < time1
        assert time1 - time2 >= 0.15

    def test_keepalive_resets_deadline(self):
        """keepalive() resets deadline to full timeout."""
        monitor = TimeoutMonitor(timeout_seconds=5.0)

        # Let some time pass
        time.sleep(0.2)
        before_keepalive = monitor.time_until_timeout()
        assert before_keepalive < 4.9  # Deadline has moved closer

        # Call keepalive
        monitor.keepalive()

        # Deadline should be reset to full timeout
        after_keepalive = monitor.time_until_timeout()
        assert 4.9 < after_keepalive <= 5.0

    def test_multiple_keepalives(self):
        """Multiple keepalive calls each reset the deadline."""
        monitor = TimeoutMonitor(timeout_seconds=3.0)

        for _ in range(3):
            time.sleep(0.1)
            monitor.keepalive()
            # Each time, deadline should be reset to full timeout
            assert 2.9 < monitor.time_until_timeout() <= 3.0

    def test_keepalive_when_disabled_is_safe(self):
        """keepalive() does nothing when timeout is disabled (None)."""
        monitor = TimeoutMonitor(timeout_seconds=None)

        # Should not raise or cause issues
        monitor.keepalive()
        assert monitor.time_until_timeout() == float("inf")

    def test_negative_time_until_timeout_when_past_deadline(self):
        """time_until_timeout() returns negative value when past deadline."""
        monitor = TimeoutMonitor(timeout_seconds=0.1)

        # Wait for deadline to pass
        time.sleep(0.15)

        time_until = monitor.time_until_timeout()
        assert time_until < 0  # Past deadline


class TestTimeoutMonitorRunTask:
    """Tests for TimeoutMonitor.run() background task."""

    @pytest.mark.asyncio
    async def test_run_raises_timeout_when_deadline_exceeded(self):
        """run() raises PolicyTimeoutError when deadline is exceeded."""
        monitor = TimeoutMonitor(timeout_seconds=0.2)

        # Don't call keepalive - let it timeout
        with pytest.raises(PolicyTimeoutError) as exc_info:
            await monitor.run()

        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_run_waits_indefinitely_when_disabled(self):
        """run() waits indefinitely when timeout is None."""
        monitor = TimeoutMonitor(timeout_seconds=None)

        # Create background task
        monitor_task = asyncio.create_task(monitor.run())

        # Wait to ensure it doesn't raise
        await asyncio.sleep(0.1)

        # Should still be running
        assert not monitor_task.done()

        # Clean up
        monitor_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor_task

    @pytest.mark.asyncio
    async def test_run_sleeps_until_deadline(self):
        """run() sleeps for the full timeout duration (not polling)."""
        monitor = TimeoutMonitor(timeout_seconds=0.3)

        start_time = time.monotonic()

        # Run monitor until timeout
        with pytest.raises(PolicyTimeoutError):
            await monitor.run()

        elapsed = time.monotonic() - start_time

        # Should have slept for approximately the full timeout
        # (not woken up multiple times for polling)
        assert 0.29 < elapsed < 0.35

    @pytest.mark.asyncio
    async def test_keepalive_wakes_and_recalculates_sleep(self):
        """keepalive() wakes the monitor to recalculate sleep time."""
        monitor = TimeoutMonitor(timeout_seconds=0.5)

        # Start monitor in background
        monitor_task = asyncio.create_task(monitor.run())

        # Call keepalive multiple times before timeout
        for _ in range(3):
            await asyncio.sleep(0.2)  # Sleep for less than timeout
            monitor.keepalive()  # Reset deadline

        # Monitor should still be running (not timed out)
        assert not monitor_task.done()

        # Clean up
        monitor_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor_task

    @pytest.mark.asyncio
    async def test_run_handles_cancellation_gracefully(self):
        """run() handles cancellation (normal shutdown) gracefully."""
        monitor = TimeoutMonitor(timeout_seconds=10.0)

        # Start monitor
        monitor_task = asyncio.create_task(monitor.run())

        # Let it start sleeping
        await asyncio.sleep(0.05)

        # Cancel it (simulating normal completion)
        monitor_task.cancel()

        # Should raise CancelledError, not PolicyTimeoutError
        with pytest.raises(asyncio.CancelledError):
            await monitor_task

    @pytest.mark.asyncio
    async def test_immediate_timeout_when_already_past_deadline(self):
        """run() immediately raises timeout if already past deadline."""
        monitor = TimeoutMonitor(timeout_seconds=0.1)

        # Wait for deadline to pass before calling run()
        time.sleep(0.15)

        # Should timeout immediately (not wait)
        start_time = time.monotonic()
        with pytest.raises(PolicyTimeoutError):
            await monitor.run()

        elapsed = time.monotonic() - start_time
        # Should be nearly instantaneous (< 50ms)
        assert elapsed < 0.05
