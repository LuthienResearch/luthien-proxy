# ABOUTME: Queue utilities for streaming chunk processing
# ABOUTME: Uses asyncio.Queue.shutdown() for clean stream termination

"""Queue utilities for streaming chunk processing.

Provides helper functions for working with asyncio.Queue in streaming contexts,
using the built-in shutdown() method for clean stream termination.
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

T = TypeVar("T")


async def get_available(queue: asyncio.Queue[T]) -> list[T]:
    """Get all currently available items from queue.

    Blocks until at least one item is available, then returns all items
    currently in the queue. Returns empty list if queue is shut down and empty.

    This is useful for batch-oriented stream processing where you want to
    process chunks together rather than one at a time.

    Args:
        queue: Queue to read from (should be shut down to signal end)

    Returns:
        List of available items (empty if queue shut down and empty)

    Example:
        async def process_stream(incoming: asyncio.Queue, outgoing: asyncio.Queue):
            while True:
                batch = await get_available(incoming)
                if not batch:  # Stream ended
                    break
                # Process batch
                for item in batch:
                    await outgoing.put(process(item))
            outgoing.shutdown()
    """
    # Block until at least one item is available
    # NOTE: trying to loop on get_nowait() first can lead to busy-waiting and indefinite blocking
    try:
        first = await queue.get()
    except asyncio.QueueShutDown:
        return []

    # Collect the first item plus any others immediately available
    items = [first]
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueShutDown:
            return items
        except asyncio.QueueEmpty:
            return items


__all__ = ["get_available"]
