# ABOUTME: Streaming orchestrator for coordinating async stream processing with timeout monitoring
# ABOUTME: Generic queue-based streaming coordinator, independent of policy implementation details

"""Streaming orchestration with timeout monitoring.

This module provides generic infrastructure for processing async streams with:
- Queue-based buffering between producer and consumer
- Timeout monitoring with keepalive signals
- Background task coordination
- Clean error handling and cancellation
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Callable, Coroutine, TypeVar

from luthien_proxy.v2.streaming import ChunkQueue

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TimeoutTracker:
    """Tracks activity and provides timeout monitoring for streaming operations."""

    def __init__(self, timeout_seconds: float):
        """Initialize timeout tracker."""
        self.timeout_seconds = timeout_seconds
        self.last_activity = time.time()

    def ping(self) -> None:
        """Record activity (resets timeout timer)."""
        self.last_activity = time.time()

    def raise_on_timeout(self) -> Coroutine[Any, Any, None]:
        """Monitor task that raises TimeoutError if timeout exceeded.

        Runs until cancelled (when streaming completes successfully) or until
        timeout is exceeded (raises TimeoutError).
        """

        async def _monitor() -> None:
            while True:
                await asyncio.sleep(1.0)
                elapsed = time.time() - self.last_activity
                if elapsed > self.timeout_seconds:
                    raise TimeoutError(f"Stream timeout: no activity for {self.timeout_seconds}s")

        return _monitor()


class StreamingOrchestrator:
    """Orchestrates streaming with queue-based processing and timeout monitoring.

    This class provides generic streaming infrastructure:
    1. Creates incoming/outgoing ChunkQueues
    2. Launches background tasks to feed incoming queue and run processor
    3. Monitors for timeout (no activity for timeout_seconds)
    4. Yields chunks from outgoing queue to caller

    The processor is policy-agnostic - it just needs to read from incoming queue,
    write to outgoing queue, and optionally call keepalive() to prevent timeout.
    """

    async def process(
        self,
        incoming_stream: AsyncIterator[T],
        processor: Callable[
            [ChunkQueue[T], ChunkQueue[T], Callable[[], None]],
            Coroutine[Any, Any, None],
        ],
        timeout_seconds: float = 30.0,
    ) -> AsyncIterator[T]:
        """Process an async stream with timeout monitoring.

        Args:
            incoming_stream: Async iterator producing chunks to process
            processor: Async callable that processes chunks. Receives:
                - incoming: ChunkQueue to read from
                - outgoing: ChunkQueue to write to
                - keepalive: Callable to signal activity (prevents timeout)
            timeout_seconds: Maximum seconds without activity before timing out

        Yields:
            Processed chunks from the outgoing queue

        Raises:
            TimeoutError: If no activity for timeout_seconds
            Exception: Any exception from processor or incoming_stream
        """
        incoming_queue: ChunkQueue[T] = ChunkQueue()
        outgoing_queue: ChunkQueue[T] = ChunkQueue()

        timeout_tracker = TimeoutTracker(timeout_seconds)
        chunk_count = 0

        try:
            async with asyncio.TaskGroup() as tg:
                # Launch background tasks
                tg.create_task(self._feed_incoming_chunks(incoming_stream, incoming_queue))
                tg.create_task(processor(incoming_queue, outgoing_queue, timeout_tracker.ping))
                monitor_task = tg.create_task(timeout_tracker.raise_on_timeout())

                # Drain outgoing queue until processor closes it
                while True:
                    batch = await outgoing_queue.get_available()
                    if not batch:
                        break

                    timeout_tracker.ping()

                    for chunk in batch:
                        chunk_count += 1
                        yield chunk

                # Streaming completed successfully
                monitor_task.cancel()

            logger.debug(f"Streaming completed successfully: {chunk_count} chunks")

        except BaseException as exc:
            # BaseException catches both regular exceptions and ExceptionGroup from TaskGroup
            logger.error(f"Streaming error after {chunk_count} chunks: {exc}")
            # TaskGroup automatically cancels all tasks on exception
            raise

    async def _feed_incoming_chunks(
        self,
        source: AsyncIterator[T],
        queue: ChunkQueue[T],
    ) -> None:
        """Feed chunks from source iterator into queue, then close it.

        This runs as a background task, continuously pulling from the source
        iterator and pushing into the queue until the source is exhausted.
        """
        try:
            async for chunk in source:
                await queue.put(chunk)
        finally:
            await queue.close()


__all__ = ["StreamingOrchestrator", "TimeoutTracker"]
