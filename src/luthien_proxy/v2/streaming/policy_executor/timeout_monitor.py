# ABOUTME: TimeoutMonitor class for managing keepalive-based timeout tracking
# ABOUTME: Provides background monitoring task and keepalive reset functionality

"""Timeout monitoring for policy execution.

This module provides a TimeoutMonitor class that tracks activity via keepalive
signals and raises an error if too much time passes without activity.
"""

import asyncio
import logging
import time

from luthien_proxy.v2.streaming.policy_executor.interface import PolicyTimeoutError

logger = logging.getLogger(__name__)

# How often to check for timeout (in seconds)
# This should be significantly smaller than typical timeout thresholds
# to ensure responsive timeout detection
TIMEOUT_CHECK_INTERVAL = 0.1


class TimeoutMonitor:
    """Monitors for timeout violations using keepalive mechanism.

    This class:
    - Tracks time since last keepalive signal
    - Provides a background monitoring task that checks for timeout
    - Raises PolicyTimeoutError when timeout threshold is exceeded
    - Can be disabled by setting timeout_seconds to None

    Usage:
        monitor = TimeoutMonitor(timeout_seconds=30.0)
        monitor_task = asyncio.create_task(monitor.run())

        # During processing
        monitor.keepalive()  # Reset timeout

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
        self._last_keepalive = time.monotonic()

    def keepalive(self) -> None:
        """Signal that processing is actively working, resetting timeout.

        Call this periodically during long-running operations to indicate
        the system hasn't stalled. Resets the internal activity timestamp.
        """
        self._last_keepalive = time.monotonic()

    def time_since_keepalive(self) -> float:
        """Get time in seconds since last keepalive (or initialization).

        Returns:
            Seconds since last keepalive() call or __init__
        """
        return time.monotonic() - self._last_keepalive

    async def run(self) -> None:
        """Background monitoring task that checks for timeout violations.

        Periodically checks if time since last keepalive exceeds the configured
        timeout threshold. If so, raises PolicyTimeoutError.

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
                await asyncio.sleep(TIMEOUT_CHECK_INTERVAL)

                time_since = self.time_since_keepalive()
                logger.debug(
                    f"Timeout monitor check: {time_since:.3f}s since keepalive (threshold: {self.timeout_seconds}s)"
                )
                if time_since > self.timeout_seconds:
                    logger.error(
                        f"Policy timeout: {time_since:.2f}s since last keepalive (threshold: {self.timeout_seconds}s)"
                    )
                    raise PolicyTimeoutError(
                        f"Policy processing timed out after {time_since:.2f}s "
                        f"without keepalive (threshold: {self.timeout_seconds}s)"
                    )
        except asyncio.CancelledError:
            # Normal cancellation when stream completes
            logger.debug("Timeout monitor cancelled (stream completed)")
            raise


__all__ = ["TimeoutMonitor", "TIMEOUT_CHECK_INTERVAL"]
