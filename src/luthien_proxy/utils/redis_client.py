"""Redis client manager that caches per-URL clients."""

from __future__ import annotations

import asyncio
import inspect
from typing import Callable

import redis.asyncio as redis  # type: ignore

RedisClient = redis.Redis
RedisFactory = Callable[[str], RedisClient]


class RedisClientManager:
    """Manage cached Redis clients with simple lifecycle helpers."""

    def __init__(self, factory: RedisFactory | None = None) -> None:
        """Create a manager that uses redis.from_url unless a factory is provided."""
        if factory is None:
            factory = redis.from_url
        self._factory: RedisFactory = factory
        self._cache: dict[str, RedisClient] = {}
        self._lock = asyncio.Lock()

    async def get_client(self, redis_url: str) -> RedisClient:
        """Return a cached client for the URL, creating it on first use."""
        if not redis_url:
            raise RuntimeError("Redis URL must be provided")

        cached = self._cache.get(redis_url)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._cache.get(redis_url)
            if cached is not None:
                return cached

            client = self._factory(redis_url)

            await client.ping()
            self._cache[redis_url] = client
            return client

    async def close_client(self, redis_url: str) -> None:
        """Close and evict the cached client for the given URL."""
        client = self._cache.pop(redis_url, None)
        if client is None:
            return
        close = getattr(client, "close", None)
        if not callable(close):
            return
        maybe_awaitable = close()
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    async def close_all(self) -> None:
        """Close and clear all cached clients."""
        urls = list(self._cache.keys())
        for url in urls:
            await self.close_client(url)

    def clear_without_closing(self) -> None:
        """Clear the cache without touching live clients (tests set their own fakes)."""
        self._cache.clear()


__all__ = ["RedisClient", "RedisClientManager"]
