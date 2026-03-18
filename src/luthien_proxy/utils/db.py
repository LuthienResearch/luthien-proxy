"""Helpers for database connectors, connections, and shared pools."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncContextManager, AsyncIterator, Awaitable, Callable, Mapping, Protocol, Sequence, cast

import asyncpg

from luthien_proxy.utils.db_sqlite import SqlitePool, create_sqlite_pool, is_sqlite_url


class ConnectionProtocol(Protocol):
    async def close(self) -> None: ...

    async def fetch(self, query: str, *args: object) -> Sequence[Mapping[str, object]]: ...

    async def fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None: ...

    async def fetchval(self, query: str, *args: object) -> object: ...

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
    """Return the default asyncpg pool factory.

    Note: asyncpg.create_pool returns a Pool object that implements __await__,
    making it compatible with Awaitable[PoolProtocol]. The type checker may not
    recognize this due to incomplete type stubs.
    """

    # asyncpg.create_pool returns Pool which implements __await__
    # Cast to satisfy type checker while maintaining runtime correctness
    async def _pool_factory(*args: object, **kwargs: object) -> PoolProtocol:
        pool = await asyncpg.create_pool(*args, **kwargs)
        return cast(PoolProtocol, pool)

    return _pool_factory


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
    """Lazily instantiate and share a single database pool.

    Auto-detects SQLite vs PostgreSQL from the URL prefix.
    """

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
        self._is_sqlite = is_sqlite_url(url)
        self._factory = factory or get_pool_factory()
        self._pool_kwargs = pool_kwargs
        self._pool: PoolProtocol | None = None
        self._sqlite_pool: SqlitePool | None = None
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        """Return the configured database URL."""
        return self._url

    @property
    def is_sqlite(self) -> bool:
        """Whether this pool uses SQLite."""
        return self._is_sqlite

    @property
    def is_postgres(self) -> bool:
        """Whether this pool uses PostgreSQL."""
        return not self._is_sqlite

    async def get_pool(self) -> PoolProtocol:
        """Return the cached connection pool, creating it on demand."""
        if self._is_sqlite:
            if self._sqlite_pool is not None:
                return cast(PoolProtocol, self._sqlite_pool)
            async with self._lock:
                if self._sqlite_pool is None:
                    self._sqlite_pool = await create_sqlite_pool(self._url)
            return cast(PoolProtocol, self._sqlite_pool)

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
        if self._is_sqlite:
            pool = self._sqlite_pool
            self._sqlite_pool = None
            if pool is not None:
                await pool.close()
        else:
            pool = self._pool
            self._pool = None
            if pool is not None:
                await pool.close()


def parse_db_ts(value: object) -> datetime:
    """Normalize a DB timestamp column to a Python datetime.

    asyncpg returns datetime objects; aiosqlite returns ISO-8601 strings.
    Both are handled transparently so callers stay DB-agnostic.

    Raises:
        TypeError: If value is neither datetime nor str.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Expected datetime or str for timestamp column, got {type(value).__name__}")


class DatabaseWriteError(Exception):
    """A database write failed.

    Wraps the underlying driver exception (asyncpg, aiosqlite, etc.) so
    callers don't need to know which DB backend is in use.

    Attributes:
        cause: The original driver exception.
    """

    def __init__(self, message: str, *, cause: BaseException) -> None:
        """Wrap a driver exception with a human-readable message."""
        super().__init__(message)
        self.cause = cause


__all__ = [
    "ConnectFn",
    "DatabaseWriteError",
    "DatabasePool",
    "PoolFactory",
    "create_pool",
    "get_connector",
    "get_pool_factory",
    "parse_db_ts",
]
