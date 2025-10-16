# ABOUTME: Streaming utilities - queue-based chunk processing for policies
# ABOUTME: Provides ChunkQueue for batch-oriented streaming policy processing

"""Streaming utilities for queue-based chunk processing.

This module provides a ChunkQueue abstraction that allows policies to:
- Pull all currently-available chunks in a batch
- Process chunks together (e.g., merge, filter, transform)
- Emit zero or more output chunks per batch
- Handle backpressure naturally through the queue
"""

from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

T = TypeVar("T")


class ChunkQueue(Generic[T]):
    """Queue for streaming chunks with batch-oriented consumption.

    This queue allows policies to consume all currently-available chunks
    at once, enabling efficient batch processing.

    Example usage:
        async def process_stream(incoming: ChunkQueue[StreamingResponse], outgoing: ChunkQueue[StreamingResponse]):
            while True:
                # Get all currently available chunks (blocks if empty)
                batch = await incoming.get_available()
                if not batch:  # Stream closed
                    break

                # Process the batch
                merged = merge_chunks(batch)
                await outgoing.put(merged)
    """

    def __init__(self, maxsize: int = 0):
        """Initialize chunk queue.

        Args:
            maxsize: Maximum queue size (0 = unlimited)
        """
        self._queue: asyncio.Queue[T | None] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    async def put(self, item: T) -> None:
        """Put an item in the queue.

        Args:
            item: The item to put

        Raises:
            ValueError: If queue is closed
        """
        if self._closed:
            raise ValueError("Cannot put to closed queue")
        await self._queue.put(item)

    async def get_available(self) -> list[T]:
        """Get all currently available items.

        This blocks until at least one item is available, then returns
        all items that are currently in the queue.

        Returns:
            List of available items (empty list if queue is closed)
        """
        if self._closed and self._queue.empty():
            return []

        # Wait for first item (blocks until available)
        first = await self._queue.get()
        if first is None:  # Sentinel for stream end
            self._closed = True
            return []

        # Collect all other immediately-available items
        items = [first]
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                if item is None:  # Sentinel
                    self._closed = True
                    break
                items.append(item)
            except asyncio.QueueEmpty:
                break

        return items

    async def close(self) -> None:
        """Close the queue (signals no more items will be added)."""
        await self._queue.put(None)  # Sentinel value
        self._closed = True

    def is_closed(self) -> bool:
        """Check if queue is closed."""
        return self._closed


__all__ = ["ChunkQueue"]
