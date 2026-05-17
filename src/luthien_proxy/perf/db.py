"""Perf-DB isolation enforcement, migration runner, and drop helpers.

The perf database is a completely isolated database used only for performance
benchmarking. It must never alias the dev database (~/.luthien/local.db).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal


def get_perf_db_url(backend: Literal["sqlite", "postgres"]) -> str:
    """Return the URL for the perf-test database.

    Args:
        backend: "sqlite" → file URL under ~/.luthien/perf.db;
            "postgres" → DATABASE_URL with perf_test schema override.

    Returns:
        A database URL string for use with the migration runner.

    Raises:
        RuntimeError: When backend is "postgres" and DATABASE_URL is unset.
    """
    if backend == "sqlite":
        return f"sqlite:///{Path.home()}/.luthien/perf.db"
    base_url = os.environ.get("DATABASE_URL", "")
    if not base_url:
        raise RuntimeError("DATABASE_URL environment variable is required for postgres backend")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options=-csearch_path=perf_test"


def ensure_perf_isolation(url: str) -> None:
    """Assert that a database URL is not the dev database.

    This is the safety gate — call it before any write to the perf DB.

    Args:
        url: The database URL to inspect.

    Raises:
        RuntimeError: If the URL points to the dev database (contains "local.db"),
            or if it is a Postgres URL without the "perf_test" schema override.
            The message always contains the word "isolation".
    """
    if "local.db" in url:
        raise RuntimeError(
            "Perf DB isolation violation: URL contains 'local.db' — "
            "refusing to use the dev database as the perf database. "
            "Use get_perf_db_url() to obtain the correct perf DB URL."
        )
    if url.startswith(("postgresql://", "postgres://")) and "perf_test" not in url:
        raise RuntimeError(
            f"Perf DB isolation violation: Postgres URL must include "
            f"'perf_test' schema (add ?options=-csearch_path=perf_test). Got: {url!r}"
        )


def drop_perf_db(backend: Literal["sqlite", "postgres"]) -> None:
    """Drop the perf database. Idempotent — safe to call when already dropped.

    Args:
        backend: "sqlite" removes ~/.luthien/perf.db (no-op if absent);
            "postgres" runs DROP SCHEMA IF EXISTS perf_test CASCADE.
    """
    if backend == "sqlite":
        perf_path = Path.home() / ".luthien" / "perf.db"
        perf_path.unlink(missing_ok=True)
        return

    # TODO: untested — implement alongside _seed_postgres in seeding.py
    url = get_perf_db_url("postgres")

    async def _drop() -> None:
        import asyncpg  # type: ignore[import-untyped]  # noqa: PLC0415

        conn = await asyncpg.connect(url)
        try:
            await conn.execute("DROP SCHEMA IF EXISTS perf_test CASCADE")
        finally:
            await conn.close()

    asyncio.run(_drop())


def migrate_perf_db(backend: Literal["sqlite", "postgres"]) -> None:
    """Apply all migrations to the perf database.

    Calls ensure_perf_isolation before touching the database. For SQLite,
    creates ~/.luthien/ if needed and runs the bundled migration scripts
    via the standard migration runner.

    Args:
        backend: "sqlite" or "postgres".

    Raises:
        RuntimeError: If isolation check fails or migrations fail.
        NotImplementedError: For the "postgres" backend (not yet implemented).
    """
    url = get_perf_db_url(backend)
    ensure_perf_isolation(url)

    if backend == "sqlite":
        _migrate_sqlite(url)
    else:
        raise NotImplementedError("Postgres perf migration is not yet implemented")


def _migrate_sqlite(url: str) -> None:
    from luthien_proxy.utils.db import DatabasePool  # noqa: PLC0415
    from luthien_proxy.utils.db_sqlite import parse_sqlite_url  # noqa: PLC0415
    from luthien_proxy.utils.migration_check import apply_sqlite_migrations  # noqa: PLC0415

    db_path = Path(parse_sqlite_url(url))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        db_pool = DatabasePool(url)
        try:
            await apply_sqlite_migrations(db_pool)
        finally:
            await db_pool.close()

    asyncio.run(_run())
