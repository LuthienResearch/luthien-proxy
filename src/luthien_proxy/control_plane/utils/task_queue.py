"""Lightweight sequential task queue for ordered async execution."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable


class SequentialTaskQueue:
    """Process submitted awaitables one-by-one in FIFO order."""

    def __init__(self, name: str) -> None:
        """Initialise an empty queue bound to *name* for logging."""
        self._name = name
        self._queue: asyncio.Queue[Awaitable[None]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._logger = logging.getLogger(__name__)

    def submit(self, coro: Awaitable[None]) -> None:
        """Schedule *coro* to run after previously queued tasks."""
        loop = asyncio.get_running_loop()
        self._queue.put_nowait(coro)
        if self._worker is None or self._worker.done():
            self._worker = loop.create_task(self._drain())

    async def _drain(self) -> None:
        """Run queued coroutines sequentially until the queue is empty."""
        while True:
            try:
                coro = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await coro
            except Exception as exc:  # pragma: no cover - diagnostic path
                self._logger.error("SequentialTaskQueue[%s] task failed: %s", self._name, exc)
            finally:
                self._queue.task_done()


DEBUG_LOG_QUEUE = SequentialTaskQueue("debug_logs")
CONVERSATION_EVENT_QUEUE = SequentialTaskQueue("conversation_events")


__all__ = [
    "SequentialTaskQueue",
    "DEBUG_LOG_QUEUE",
    "CONVERSATION_EVENT_QUEUE",
]
