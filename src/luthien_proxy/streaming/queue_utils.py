"""Shared queue utilities for streaming pipeline stages."""

import asyncio
import logging
from typing import TypeVar

from luthien_proxy.utils.constants import QUEUE_PUT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def safe_put(queue: asyncio.Queue[T], item: T, context: str = "downstream") -> None:
    """Put item in queue with timeout to prevent deadlock.

    Args:
        queue: Queue to put item into
        item: Item to put
        context: Description of the consumer for error messages

    Raises:
        asyncio.TimeoutError: If queue is full and timeout is exceeded
    """
    try:
        await asyncio.wait_for(queue.put(item), timeout=QUEUE_PUT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error(f"Queue put timeout after {QUEUE_PUT_TIMEOUT_SECONDS}s - {context} may be slow or stalled")
        raise


__all__ = ["safe_put"]
