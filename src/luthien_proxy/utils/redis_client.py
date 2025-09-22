"""Shared Redis client management with simple caching."""

from __future__ import annotations

import asyncio
import inspect

import redis.asyncio as redis  # type: ignore

RedisClient = redis.Redis

_client_cache: dict[str, RedisClient] = {}
_cache_lock = asyncio.Lock()


async def get_client(redis_url: str) -> RedisClient:
    """Return a cached Redis client for the given URL, creating it on first use."""
    if not redis_url:
        raise RuntimeError("Redis URL must be provided")

    client = _client_cache.get(redis_url)
    if client is not None:
        return client

    async with _cache_lock:
        client = _client_cache.get(redis_url)
        if client is not None:
            return client

        created = redis.from_url(redis_url)
        if created is None:
            raise RuntimeError("Failed to create Redis client")

        await created.ping()
        _client_cache[redis_url] = created
        return created


async def close_client(redis_url: str) -> None:
    """Close and evict the cached client for the given URL."""
    client = _client_cache.pop(redis_url, None)
    if client is None:
        return
    close = getattr(client, "close", None)
    if callable(close):
        maybe_awaitable = close()
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable


async def close_all() -> None:
    """Close and remove all cached redis clients (primarily for tests)."""
    urls = list(_client_cache.keys())
    for url in urls:
        await close_client(url)


def _reset_cache() -> None:
    """Reset the internal cache without closing clients (tests inject fakes)."""
    _client_cache.clear()


__all__ = ["get_client", "close_client", "close_all", "RedisClient"]
