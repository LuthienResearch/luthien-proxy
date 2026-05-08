"""In-process asyncio-safe token bucket rate limiter for per-key request limiting."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time

from fastapi import HTTPException


class TokenBucketRateLimiter:
    """Per-key token bucket rate limiter.

    Uses one asyncio.Lock per key to ensure concurrency safety without a global
    bottleneck. A meta-lock serialises only the short dict lookup/creation step.

    Keys are SHA-256 hashed before storage so raw credential values are never
    held in memory.

    RPM=0 disables limiting entirely (all requests pass through unchecked).
    """

    def __init__(self, rpm: int, burst: int, max_keys: int = 10_000) -> None:
        """Initialise the rate limiter.

        Args:
            rpm: Requests per minute. 0 disables rate limiting.
            burst: Maximum token accumulation above RPM. 0 defaults to rpm.
            max_keys: Maximum number of per-key buckets to retain (LRU-style
                eviction removes the oldest entry when exceeded).
        """
        self.rpm = rpm
        self.burst = burst if burst > 0 else rpm
        self.max_keys = max_keys
        # Per-key state: (asyncio.Lock, mutable_state)
        # mutable_state is [tokens: float, last_refill_time: float]
        # Keys are SHA-256 hex digests — raw credential values are never stored.
        self._buckets: dict[str, tuple[asyncio.Lock, list[float]]] = {}
        self._meta_lock = asyncio.Lock()

    def _hash_key(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    async def _get_or_create_bucket(self, key: str) -> tuple[asyncio.Lock, list[float]]:
        # Fast-path: bucket already exists, no lock needed.
        bucket = self._buckets.get(key)
        if bucket is not None:
            return bucket
        async with self._meta_lock:
            if key not in self._buckets:
                self._buckets[key] = (asyncio.Lock(), [float(self.burst), time.monotonic()])
                # LRU-style eviction: drop oldest insertion when over cap.
                if len(self._buckets) > self.max_keys:
                    self._buckets.pop(next(iter(self._buckets)))
            return self._buckets[key]

    async def check(self, key: str) -> None:
        """Check whether key is within rate limit, raising HTTP 429 if exceeded.

        Args:
            key: Rate limit key (e.g. credential token value). Hashed internally.

        Raises:
            HTTPException: 429 with Retry-After, X-RateLimit-* headers if exceeded.
        """
        if self.rpm == 0:
            return

        hashed = self._hash_key(key)
        lock, state = await self._get_or_create_bucket(hashed)

        async with lock:
            now = time.monotonic()
            tokens, last_time = state[0], state[1]
            elapsed = now - last_time
            tokens = min(float(self.burst), tokens + elapsed * (self.rpm / 60.0))
            state[1] = now

            if tokens < 1.0:
                tokens_needed = 1.0 - tokens
                retry_after = math.ceil(tokens_needed / (self.rpm / 60.0))
                # Wall-clock time for X-RateLimit-Reset: clients expect a unix timestamp,
                # not a monotonic offset.
                reset_unix = int(time.time()) + retry_after
                state[0] = tokens
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(self.rpm),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(reset_unix),
                    },
                )

            state[0] = tokens - 1.0


__all__ = ["TokenBucketRateLimiter"]
