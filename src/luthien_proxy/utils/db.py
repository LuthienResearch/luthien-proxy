"""Minimal helpers for database connectors and connections."""

from __future__ import annotations

import inspect
import os
from typing import Any, Awaitable, Callable

import asyncpg

ConnectFn = Callable[[str], Awaitable[Any]]
PoolFactory = Callable[..., Awaitable[Any]]


def database_url() -> str:
    """Return the DATABASE_URL or a local default."""
    return os.getenv("DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien")


def get_connector() -> ConnectFn:
    """Return the default asyncpg connector."""
    return asyncpg.connect


def get_pool_factory() -> PoolFactory:
    """Return the default asyncpg pool factory."""
    return asyncpg.create_pool


async def open_connection(connect: ConnectFn | None = None, url: str | None = None) -> Any:
    """Open a database connection using the provided connector."""
    connector = connect or get_connector()
    return await connector(url or database_url())


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
    pool_factory = factory or get_pool_factory()
    return await pool_factory(url or database_url(), **kwargs)
