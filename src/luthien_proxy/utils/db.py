"""Helpers for database connectors, connections, and shared pools."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncContextManager, AsyncIterator, Awaitable, Callable, Mapping, Protocol, Sequence

import asyncpg


class ConnectionProtocol(Protocol):
    async def close(self) -> None: ...

    async def fetch(self, query: str, *args: object) -> Sequence[Mapping[str, object]]: ...

    async def fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None: ...

    async def execute(self, query: str, *args: object) -> object: ...

    def transaction(self) -> AsyncContextManager[None]: ...


class PoolProtocol(Protocol):
    def acquire(self) -> AsyncContextManager[ConnectionProtocol]: ...

    async def close(self) -> None: ...

    async def fetch(self, query: str, *args: object) -> Sequence[Mapping[str, object]]: ...

    async def fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None: ...

    async def execute(self, query: str, *args: object) -> object: ...


ConnectFn = Callable[[str], Awaitable[ConnectionProtocol]]
PoolFactory = Callable[..., Awaitable[PoolProtocol]]


def get_connector() -> ConnectFn:
    """Return the default asyncpg connector."""
    return asyncpg.connect


def get_pool_factory() -> PoolFactory:
    """Return the default asyncpg pool factory."""
    return asyncpg.create_pool


async def open_connection(connect: ConnectFn | None = None, url: str | None = None) -> ConnectionProtocol:
    """Open a database connection using the provided connector."""
    if url is None:
        raise RuntimeError("Database URL must be provided")
    connector = connect or get_connector()
    return await connector(url)


async def close_connection(conn: ConnectionProtocol) -> None:
    """Close a database connection."""
    await conn.close()


async def create_pool(
    factory: PoolFactory | None = None,
    url: str | None = None,
    **kwargs: object,
) -> PoolProtocol:
    """Create a connection pool using the provided factory."""
    if url is None:
        raise RuntimeError("Database URL must be provided")
    pool_factory = factory or get_pool_factory()
    return await pool_factory(url, **kwargs)


class DatabasePool:
    """Lazily instantiate and share a single asyncpg pool per database URL."""

    def __init__(
        self,
        url: str,
        *,
        factory: PoolFactory | None = None,
        **pool_kwargs: object,
    ) -> None:
        """Initialize the database connection pool."""
        if not url:
            raise RuntimeError("Database URL must be provided")
        self._url = url
        self._factory = factory or get_pool_factory()
        self._pool_kwargs = pool_kwargs
        self._pool: PoolProtocol | None = None
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        """Return the configured database URL."""
        return self._url

    async def get_pool(self) -> PoolProtocol:
        """Return the cached connection pool, creating it on demand."""
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                self._pool = await self._factory(self._url, **self._pool_kwargs)
        return self._pool

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[ConnectionProtocol]:
        """Yield a connection from the shared pool."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            yield conn

    async def close(self) -> None:
        """Close the underlying pool and reset internal state."""
        pool = self._pool
        self._pool = None
        if pool is None:
            return
        await pool.close()


__all__ = [
    "ConnectFn",
    "PoolFactory",
    "get_connector",
    "get_pool_factory",
    "open_connection",
    "close_connection",
    "create_pool",
    "DatabasePool",
]
