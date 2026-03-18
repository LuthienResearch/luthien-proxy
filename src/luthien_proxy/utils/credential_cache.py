"""Credential cache protocol and implementations.

Provides a protocol for TTL key-value caching used by CredentialManager,
with both Redis-backed and in-process implementations.
"""

from __future__ import annotations

import fnmatch
import time
from typing import AsyncIterator, Protocol, runtime_checkable

import redis.asyncio as redis


@runtime_checkable
class CredentialCacheProtocol(Protocol):
    """Protocol for credential validation caching with TTL support."""

    async def get(self, key: str) -> str | None:
        """Get a cached value by key."""
        ...

    async def setex(self, key: str, ttl: int, value: str) -> None:
        """Set a key with TTL in seconds."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete a key, returning True if it existed."""
        ...

    async def ttl(self, key: str) -> int:
        """Return remaining TTL in seconds, or -2 if key doesn't exist."""
        ...

    def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        """Yield keys matching a glob pattern."""
        ...

    async def unlink(self, *keys: str) -> int:
        """Delete multiple keys, returning the count of keys removed."""
        ...


class InProcessCredentialCache:
    """In-process TTL cache for single-process local mode.

    Stores entries as (value, expiry_timestamp). Expired entries are
    cleaned up lazily on read and during scan.
    """

    def __init__(self) -> None:
        """Initialize empty cache."""
        self._data: dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> str | None:
        """Get a cached value by key, returning None if expired or missing."""
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._data[key]
            return None
        return value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        """Set a key with TTL in seconds."""
        self._data[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str) -> bool:
        """Delete a key, returning True if it existed."""
        return self._data.pop(key, None) is not None

    async def ttl(self, key: str) -> int:
        """Return remaining TTL in seconds, or -2 if key doesn't exist."""
        entry = self._data.get(key)
        if entry is None:
            return -2
        _, expires_at = entry
        remaining = int(expires_at - time.monotonic())
        if remaining <= 0:
            del self._data[key]
            return -2
        return remaining

    async def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        """Yield keys matching a glob pattern, skipping expired entries."""
        now = time.monotonic()
        expired: list[str] = []
        for key, (_, expires_at) in self._data.items():
            if now >= expires_at:
                expired.append(key)
                continue
            if fnmatch.fnmatch(key, match):
                yield key
        for key in expired:
            del self._data[key]

    async def unlink(self, *keys: str) -> int:
        """Delete multiple keys, returning the count of keys removed."""
        count = 0
        for key in keys:
            if self._data.pop(key, None) is not None:
                count += 1
        return count


class RedisCredentialCache:
    """Redis-backed credential cache. Thin wrapper matching the protocol.

    All methods delegate directly to the Redis client, decoding bytes to str
    where needed since we use decode_responses=False.
    """

    def __init__(self, client: redis.Redis) -> None:
        """Initialize with a Redis client."""
        self._redis = client

    async def get(self, key: str) -> str | None:
        """Get a cached value by key."""
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return raw if isinstance(raw, str) else raw.decode()

    async def setex(self, key: str, ttl: int, value: str) -> None:
        """Set a key with TTL in seconds."""
        await self._redis.setex(key, ttl, value)

    async def delete(self, key: str) -> bool:
        """Delete a key, returning True if it existed."""
        return (await self._redis.delete(key)) > 0

    async def ttl(self, key: str) -> int:
        """Return remaining TTL in seconds, or -2 if key doesn't exist."""
        return await self._redis.ttl(key)

    async def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        """Yield keys matching a glob pattern."""
        async for key in self._redis.scan_iter(match=match):
            yield key if isinstance(key, str) else key.decode()

    async def unlink(self, *keys: str) -> int:
        """Delete multiple keys, returning the count of keys removed."""
        return int(await self._redis.unlink(*keys))
