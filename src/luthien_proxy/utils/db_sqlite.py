"""SQLite adapter implementing the same ConnectionProtocol/PoolProtocol as asyncpg.

Allows the application to run without PostgreSQL/Docker by using a local SQLite file.
Designed for single-user local development, not production multi-user deployments.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Mapping, Sequence

import aiosqlite

_STRING_LITERAL_DOLLAR_N = re.compile(
    r"""
    '(?:''|[^'])*'      # single-quoted string literal (SQL-standard, '' = escaped quote)
    |
    "(?:""|[^"])*"      # double-quoted identifier/literal
    """,
    re.VERBOSE,
)
_DOLLAR_N = re.compile(r"\$(\d+)")


def _reject_dollar_n_in_literals(query: str) -> None:
    """Raise if a quoted string/identifier contains a $N-looking token.

    The translator's $N -> ? substitution is regex-based and does not parse SQL.
    A literal like '$5' would be rewritten as '?' and either silently corrupt
    arg binding (pre-PR #600) or raise IndexError (post-PR #600). Callers that
    genuinely need a literal '$' followed by digits must concatenate the digits
    separately (e.g. '$' || '5') or avoid the translator entirely.
    """
    for match in _STRING_LITERAL_DOLLAR_N.finditer(query):
        literal = match.group(0)
        if _DOLLAR_N.search(literal):
            raise ValueError(
                f"SQL string literal contains a $N-looking token ({literal!r}). "
                "The SQLite translator cannot distinguish this from a parameter "
                "placeholder. Rewrite the literal (e.g. concatenate '$' with the "
                "digits separately) or avoid routing this query through the SQLite "
                "adapter."
            )


def _translate_params(query: str, args: tuple[object, ...]) -> tuple[str, tuple[object, ...]]:
    """Translate asyncpg-style $1,$2 parameters to SQLite ? placeholders.

    Handles positional reuse (e.g. `VALUES ($1, $1)`): each occurrence of $N
    maps to one `?` in the output and the args tuple is rebuilt in the order
    `?` placeholders appear, so the N-th `?` gets args[ordered[N-1]-1].
    asyncpg/Postgres accept reuse natively; SQLite's `?` is strictly positional
    and does not, so we expand on the args side.

    Does NOT parse SQL. `$N`-substitution, `::` cast stripping, and LEAST/NOW
    rewriting all run as regex over the raw query. If a quoted literal contains
    a `$N`-looking token, the translator rejects the query up-front with a
    ValueError rather than silently corrupting arg binding. SQL comments
    (`--`, `/* */`) are also not parsed; a `$N` inside a comment will be
    rewritten to `?`, which is usually harmless but can still shift arg counts.

    Also rewrites PostgreSQL-specific SQL constructs to SQLite equivalents.
    """
    _reject_dollar_n_in_literals(query)

    # Walk $N occurrences left-to-right, emitting one ? per occurrence and
    # recording which original arg (1-indexed) each one consumes.
    consumed: list[int] = []

    def _sub_placeholder(match: re.Match[str]) -> str:
        n = int(match.group(1))
        # asyncpg placeholders are 1-indexed; $0 would silently map to args[-1]
        # via Python's negative indexing without this guard.
        if n < 1:
            raise ValueError(f"Invalid parameter placeholder ${n}: asyncpg placeholders are 1-indexed")
        if n > len(args):
            raise ValueError(f"Parameter ${n} exceeds number of provided arguments ({len(args)})")
        consumed.append(n)
        return "?"

    translated = _DOLLAR_N.sub(_sub_placeholder, query)
    if consumed:
        reordered = tuple(args[idx - 1] for idx in consumed)
    else:
        reordered = args

    # Strip PostgreSQL type casts (::jsonb, ::text, ::float, ::int[], etc.)
    translated = re.sub(r"::\w+(\[\])?", "", translated)

    # LEAST(a, b) → MIN(a, b)
    translated = translated.replace("LEAST(", "MIN(")

    # to_timestamp(?) → datetime(?, 'unixepoch')
    translated = re.sub(r"to_timestamp\(\?\)", "datetime(?, 'unixepoch')", translated)

    # NOW() → datetime('now')
    translated = re.sub(r"\bNOW\(\)", "datetime('now')", translated, flags=re.IGNORECASE)

    # ILIKE → LIKE (SQLite LIKE is case-insensitive for ASCII by default)
    translated = translated.replace(" ILIKE ", " LIKE ")

    return translated, reordered


def _convert_arg(value: object) -> object:
    """Convert Python values to SQLite-compatible types."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, dict | list):
        return json.dumps(value)
    return value


def _convert_args(args: tuple[object, ...]) -> tuple[object, ...]:
    """Convert all args to SQLite-compatible types."""
    return tuple(_convert_arg(a) for a in args)


class _RowProxy(Mapping[str, object]):
    """Dict-like wrapper around aiosqlite.Row for compatibility with asyncpg Record."""

    def __init__(self, keys: tuple[str, ...], values: Sequence[object]) -> None:
        self._data = dict(zip(keys, values))

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"_RowProxy({self._data!r})"


class SqliteConnection:
    """Wraps an aiosqlite connection to match ConnectionProtocol."""

    def __init__(self, conn: aiosqlite.Connection) -> None:  # noqa: D107
        self._conn = conn
        self._in_transaction = False

    async def close(self) -> None:
        """Close the underlying connection."""
        await self._conn.close()

    async def fetch(self, query: str, *args: object) -> Sequence[Mapping[str, object]]:
        """Execute query and return all rows."""
        translated, targs = _translate_params(query, args)
        targs = _convert_args(targs)
        cursor = await self._conn.execute(translated, targs)
        rows = await cursor.fetchall()
        if not rows or cursor.description is None:
            return []
        keys = tuple(d[0] for d in cursor.description)
        return [_RowProxy(keys, row) for row in rows]

    async def fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None:
        """Execute query and return the first row."""
        translated, targs = _translate_params(query, args)
        targs = _convert_args(targs)
        cursor = await self._conn.execute(translated, targs)
        row = await cursor.fetchone()
        if row is None or cursor.description is None:
            return None
        keys = tuple(d[0] for d in cursor.description)
        return _RowProxy(keys, row)

    async def fetchval(self, query: str, *args: object) -> object:
        """Execute query and return the first column of the first row."""
        row = await self.fetchrow(query, *args)
        if row is None:
            return None
        return next(iter(row.values()))

    async def execute(self, query: str, *args: object) -> object:
        """Execute a query (INSERT/UPDATE/DELETE)."""
        translated, targs = _translate_params(query, args)
        targs = _convert_args(targs)
        cursor = await self._conn.execute(translated, targs)
        if not self._in_transaction:
            await self._conn.commit()
        return f"OK {cursor.rowcount}"

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Context manager for explicit transactions."""
        self._in_transaction = True
        await self._conn.execute("BEGIN")
        try:
            yield
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        finally:
            self._in_transaction = False


class SqlitePool:
    """Lightweight pool that serializes access to a single SQLite connection.

    SQLite only supports one writer at a time, so we use a lock to serialize access.
    For the single-user local dev use case this is fine.
    """

    def __init__(self, db_path: str) -> None:  # noqa: D107
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            # WAL mode for better concurrent read performance
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            # Return rows as tuples (we wrap them in _RowProxy)
            self._conn.row_factory = None  # type: ignore[assignment]
        return self._conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[SqliteConnection]:
        """Yield a connection, serializing access with a lock."""
        async with self._lock:
            conn = await self._get_conn()
            yield SqliteConnection(conn)

    async def close(self) -> None:
        """Close the underlying connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def fetch(self, query: str, *args: object) -> Sequence[Mapping[str, object]]:
        """Execute query and return all rows."""
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None:
        """Execute query and return the first row."""
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def execute(self, query: str, *args: object) -> object:
        """Execute a query (INSERT/UPDATE/DELETE)."""
        async with self.acquire() as conn:
            return await conn.execute(query, *args)


def parse_sqlite_url(url: str) -> str:
    """Extract file path from a sqlite:// URL.

    Supports:
      sqlite:///path/to/db.sqlite  → /path/to/db.sqlite
      sqlite:///./relative.db      → ./relative.db
      sqlite://:memory:            → :memory:
    """
    if url == "sqlite://:memory:":
        return ":memory:"
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return url[len(prefix) :]
    raise ValueError(f"Invalid SQLite URL: {url}. Expected sqlite:///path or sqlite://:memory:")


async def create_sqlite_pool(url: str) -> SqlitePool:
    """Create a SqlitePool from a sqlite:// URL."""
    db_path = parse_sqlite_url(url)

    # Ensure parent directory exists for file-based databases
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    pool = SqlitePool(db_path)
    # Eagerly open connection to validate the path
    async with pool.acquire():
        pass
    return pool


def is_sqlite_url(url: str) -> bool:
    """Check if a DATABASE_URL points to SQLite."""
    return url.startswith("sqlite://")


__all__ = [
    "SqliteConnection",
    "SqlitePool",
    "create_sqlite_pool",
    "is_sqlite_url",
    "parse_sqlite_url",
]
