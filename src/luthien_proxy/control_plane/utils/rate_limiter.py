"""Asynchronous rate limiting utilities for FastAPI dependencies."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict


class RateLimitExceeded(Exception):
    """Raised when a rate limit would be exceeded."""


@dataclass
class _Bucket:
    timestamps: Deque[float]


class RateLimiter:
    """Simple async token bucket rate limiter keyed by string identifiers."""

    def __init__(self, max_events: int, window_seconds: float) -> None:
        """Initialise a RateLimiter allowing max_events per window_seconds."""
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_events = max_events
        self._window = window_seconds
        self._lock = asyncio.Lock()
        self._buckets: Dict[str, _Bucket] = {}

    async def try_acquire(self, key: str) -> bool:
        """Attempt to record an event for key, returning False if rate limited."""
        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(deque())
                self._buckets[key] = bucket
            timestamps = bucket.timestamps
            cutoff = now - self._window
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self._max_events:
                return False
            timestamps.append(now)
            return True

    async def acquire_or_raise(self, key: str) -> None:
        """Acquire a slot for key or raise RateLimitExceeded."""
        allowed = await self.try_acquire(key)
        if not allowed:
            raise RateLimitExceeded(f"Rate limit exceeded for key: {key}")

    def clear(self) -> None:
        """Reset all rate limit buckets (used in tests)."""
        self._buckets.clear()


__all__ = ["RateLimiter", "RateLimitExceeded"]
