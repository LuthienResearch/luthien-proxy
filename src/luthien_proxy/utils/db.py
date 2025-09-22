"""Minimal helpers for database connectors and connections."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

import asyncpg

ConnectFn = Callable[[str], Awaitable[Any]]
PoolFactory = Callable[..., Awaitable[Any]]


def get_connector() -> ConnectFn:
    """Return the default asyncpg connector."""
    return asyncpg.connect


def get_pool_factory() -> PoolFactory:
    """Return the default asyncpg pool factory."""
    return asyncpg.create_pool


async def open_connection(connect: ConnectFn | None = None, url: str | None = None) -> Any:
    """Open a database connection using the provided connector."""
    if url is None:
        raise RuntimeError("Database URL must be provided")
    connector = connect or get_connector()
    return await connector(url)


async def close_connection(conn: Any) -> None:
    """Close a database connection if it supports an async close."""
    close = getattr(conn, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def create_pool(
    factory: PoolFactory | None = None,
    url: str | None = None,
    **kwargs: Any,
) -> Any:
    """Create a connection pool using the provided factory."""
    if url is None:
        raise RuntimeError("Database URL must be provided")
    pool_factory = factory or get_pool_factory()
    return await pool_factory(url, **kwargs)
