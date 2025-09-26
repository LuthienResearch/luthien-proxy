"""Lightweight async rate limiter utilities for the control plane."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque, Dict


class SlidingWindowRateLimiter:
    """Simple sliding-window limiter keyed by identifier."""

    def __init__(self) -> None:
        self._events: Dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(
        self,
        key: str,
        max_events: int,
        window_seconds: float,
    ) -> bool:
        """Return True if the request should proceed for the given key."""

        if max_events <= 0 or window_seconds <= 0:
            return True

        now = time.monotonic()
        async with self._lock:
            bucket = self._events.setdefault(key, deque())
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= max_events:
                return False
            bucket.append(now)
            return True


__all__ = ["SlidingWindowRateLimiter"]
