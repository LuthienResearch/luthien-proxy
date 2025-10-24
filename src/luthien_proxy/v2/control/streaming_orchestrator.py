# ABOUTME: Streaming orchestrator for coordinating async stream processing with timeout monitoring
# ABOUTME: Generic queue-based streaming coordinator with optional OpenTelemetry tracing

"""Streaming orchestration with timeout monitoring and optional tracing.

This module provides generic infrastructure for processing async streams with:
- Queue-based buffering between producer and consumer
- Timeout monitoring with keepalive signals
- Background task coordination
- Clean error handling and cancellation
- Optional OpenTelemetry span creation
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Coroutine, TypeVar

from opentelemetry import trace

from luthien_proxy.v2.control.queue_utils import get_available

if TYPE_CHECKING:
    from opentelemetry.trace import Span

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

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
    1. Creates incoming/outgoing asyncio.Queues
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
            [asyncio.Queue[T | None], asyncio.Queue[T | None], Callable[[], None]],
            Coroutine[Any, Any, None],
        ],
        timeout_seconds: float = 30.0,
        span: Span | None = None,
        on_complete: Callable[[list[T]], Coroutine[Any, Any, None]] | None = None,
    ) -> AsyncIterator[T]:
        """Process an async stream with timeout monitoring and optional tracing.

        Args:
            incoming_stream: Async iterator producing chunks to process
            processor: Async callable that processes chunks. Receives:
                - incoming: asyncio.Queue to read from (None = stream end)
                - outgoing: asyncio.Queue to write to (put None when done)
                - keepalive: Callable to signal activity (prevents timeout)
            timeout_seconds: Maximum seconds without activity before timing out
            span: Optional OpenTelemetry span for tracing stream processing
            on_complete: Optional callback invoked after streaming completes with all buffered chunks

        Yields:
            Processed chunks from the outgoing queue

        Raises:
            TimeoutError: If no activity for timeout_seconds
            Exception: Any exception from processor or incoming_stream

        Note:
            If on_complete is provided, all chunks are buffered in memory.
            This is useful for event emission but adds memory overhead.
        """
        incoming_queue: asyncio.Queue[T | None] = asyncio.Queue()
        outgoing_queue: asyncio.Queue[T | None] = asyncio.Queue()

        timeout_tracker = TimeoutTracker(timeout_seconds)
        chunk_count = 0

        # Buffer chunks if callback is provided
        buffered_chunks: list[T] = [] if on_complete else []

        if span:
            span.add_event("orchestrator.start")
            span.set_attribute("orchestrator.timeout_seconds", timeout_seconds)

        try:
            async with asyncio.TaskGroup() as tg:
                # Launch background tasks
                tg.create_task(self._feed_incoming_chunks(incoming_stream, incoming_queue))
                tg.create_task(processor(incoming_queue, outgoing_queue, timeout_tracker.ping))
                monitor_task = tg.create_task(timeout_tracker.raise_on_timeout())

                # Drain outgoing queue until processor closes it
                while True:
                    batch = await get_available(outgoing_queue)
                    if not batch:
                        break

                    timeout_tracker.ping()

                    for chunk in batch:
                        if chunk is not None:
                            chunk_count += 1
                            # Buffer chunk if callback provided (passive buffering)
                            if on_complete:
                                buffered_chunks.append(chunk)
                            yield chunk

                # Streaming completed successfully
                monitor_task.cancel()

            logger.debug(f"Streaming completed successfully: {chunk_count} chunks")

            if span:
                span.add_event("orchestrator.complete", attributes={"chunk_count": chunk_count})
                span.set_attribute("orchestrator.chunk_count", chunk_count)
                span.set_attribute("orchestrator.success", True)

            # Invoke callback with buffered chunks (non-blocking event emission)
            if on_complete and buffered_chunks:
                try:
                    await on_complete(buffered_chunks)
                except Exception as callback_exc:
                    # Log but don't fail the stream
                    logger.error(f"on_complete callback failed: {callback_exc}")

        except BaseException as exc:
            # BaseException catches both regular exceptions and ExceptionGroup from TaskGroup
            logger.error(f"Streaming error after {chunk_count} chunks: {exc}")

            if span:
                span.add_event(
                    "orchestrator.error",
                    attributes={
                        "error.type": type(exc).__name__,
                        "error.message": str(exc),
                        "chunk_count": chunk_count,
                    },
                )
                span.set_attribute("orchestrator.chunk_count", chunk_count)
                span.set_attribute("orchestrator.success", False)
                span.record_exception(exc)

            # TaskGroup automatically cancels all tasks on exception
            raise

    async def _feed_incoming_chunks(
        self,
        source: AsyncIterator[T],
        queue: asyncio.Queue[T | None],
    ) -> None:
        """Feed chunks from source iterator into queue, then close it.

        This runs as a background task, continuously pulling from the source
        iterator and pushing into the queue until the source is exhausted.
        """
        try:
            async for chunk in source:
                await queue.put(chunk)
        finally:
            queue.shutdown()


__all__ = ["StreamingOrchestrator", "TimeoutTracker"]
