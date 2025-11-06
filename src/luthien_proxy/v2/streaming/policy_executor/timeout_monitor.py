# ABOUTME: TimeoutMonitor class for managing keepalive-based timeout tracking
# ABOUTME: Uses deadline-based approach to minimize unnecessary wake-ups

"""Timeout monitoring for policy execution.

This module provides a TimeoutMonitor class that tracks activity via keepalive
signals and raises an error if too much time passes without activity.

The monitor uses a deadline-based approach: it sets a deadline timestamp
(now + timeout_seconds) and sleeps until that deadline. Keepalive calls
reset the deadline, waking the monitor to sleep again with the new deadline.
This minimizes unnecessary wake-ups compared to polling approaches.
"""

import asyncio
import logging
import time

from luthien_proxy.v2.streaming.policy_executor.interface import PolicyTimeoutError

logger = logging.getLogger(__name__)


class TimeoutMonitor:
    """Monitors for timeout violations using keepalive mechanism with deadline-based sleeping.

    This class:
    - Tracks a deadline timestamp for when timeout should occur
    - Sleeps until the deadline (minimizing wake-ups)
    - Keepalive calls reset the deadline and wake the monitor
    - Raises PolicyTimeoutError when current time exceeds deadline
    - Can be disabled by setting timeout_seconds to None

    Usage:
        monitor = TimeoutMonitor(timeout_seconds=30.0)
        monitor_task = asyncio.create_task(monitor.run())

        # During processing
        monitor.keepalive()  # Reset deadline to now + timeout_seconds

        # When done
        monitor_task.cancel()
    """

    def __init__(self, timeout_seconds: float | None = None) -> None:
        """Initialize timeout monitor.

        Args:
            timeout_seconds: Maximum time between keepalive calls before timeout.
                If None, timeout monitoring is disabled.
        """
        self.timeout_seconds = timeout_seconds
        # Set initial deadline to now + timeout_seconds
        self._deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else float("inf")
        # Event to signal deadline updates (from keepalive calls)
        self._deadline_updated = asyncio.Event()

    def keepalive(self) -> None:
        """Signal that processing is actively working, resetting timeout deadline.

        Call this periodically during long-running operations to indicate
        the system hasn't stalled. Resets the deadline to now + timeout_seconds
        and wakes the monitor to recalculate sleep time.
        """
        if self.timeout_seconds is not None:
            self._deadline = time.monotonic() + self.timeout_seconds
            self._deadline_updated.set()

    def time_until_timeout(self) -> float:
        """Get time in seconds until timeout deadline.

        Returns:
            Seconds until deadline (negative if already past deadline)
        """
        return self._deadline - time.monotonic()

    async def run(self) -> None:
        """Background monitoring task that sleeps until timeout deadline.

        Sleeps until the deadline, then raises PolicyTimeoutError. If keepalive
        is called during sleep, wakes up and recalculates sleep time with new deadline.

        This runs continuously until cancelled (on completion or error).

        Raises:
            PolicyTimeoutError: If timeout threshold is exceeded
        """
        if self.timeout_seconds is None:
            # No timeout configured - sleep forever (will be cancelled on completion)
            await asyncio.Event().wait()
            return

        try:
            while True:
                # Calculate time until deadline
                time_until = self._deadline - time.monotonic()

                if time_until <= 0:
                    # Already past deadline - timeout!
                    logger.error(
                        f"Policy timeout: deadline exceeded by {-time_until:.2f}s (threshold: {self.timeout_seconds}s)"
                    )
                    raise PolicyTimeoutError(f"Policy processing timed out (threshold: {self.timeout_seconds}s)")

                logger.debug(f"Timeout monitor sleeping for {time_until:.3f}s until deadline")

                # Clear the event before sleeping
                self._deadline_updated.clear()

                # Sleep until deadline or until deadline is updated by keepalive
                try:
                    await asyncio.wait_for(self._deadline_updated.wait(), timeout=time_until)
                    # Event was set - deadline was updated by keepalive, loop to recalculate
                    logger.debug("Deadline updated by keepalive, recalculating sleep time")
                except asyncio.TimeoutError:
                    # Sleep completed - deadline reached, loop will raise timeout
                    logger.debug("Deadline reached, checking for timeout")

        except asyncio.CancelledError:
            # Normal cancellation when stream completes
            logger.debug("Timeout monitor cancelled (stream completed)")
            raise


__all__ = ["TimeoutMonitor"]
