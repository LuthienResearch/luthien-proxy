"""Verify SQLite and PostgreSQL migration sets produce equivalent schemas.

Applies all migrations from both directories to their respective databases,
extracts the resulting schemas, and compares table/column/index structure
with dialect-aware type normalization.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import asyncpg
import pytest

MIGRATIONS_ROOT = Path(__file__).resolve().parents[3] / "migrations"

# Dialect type normalization: map Postgres types to their SQLite equivalents
PG_TO_SQLITE_TYPES: dict[str, str] = {
    "boolean": "integer",
    "bool": "integer",
    "jsonb": "text",
    "json": "text",
    "timestamp without time zone": "text",
    "timestamp with time zone": "text",
    "uuid": "text",
    "double precision": "real",
    "bigint": "integer",
    "integer": "integer",
    "text": "text",
    "real": "real",
}


def normalize_pg_type(pg_type: str) -> str:
    """Normalize a Postgres type to its SQLite equivalent for comparison."""
    return PG_TO_SQLITE_TYPES.get(pg_type.lower(), pg_type.lower())


def get_sqlite_schema(db_path: str = ":memory:") -> dict:
    """Apply SQLite migrations and extract schema."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    sqlite_dir = MIGRATIONS_ROOT / "sqlite"
    for mf in sorted(sqlite_dir.glob("*.sql")):
        sql = mf.read_text()
        # Use executescript for multi-statement SQL (handles comments correctly)
        conn.executescript(sql)

    # Extract tables and columns
    tables = {}
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    for row in rows:
        table_name = row["name"]
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        tables[table_name] = {
            "columns": {
                col["name"]: {
                    "type": col["type"].lower(),
                    "notnull": bool(col["notnull"]),
                }
                for col in cols
            }
        }

    conn.close()
    return tables


async def get_postgres_schema() -> dict:
    """Apply Postgres migrations and extract schema."""
    dsn = os.environ.get("DATABASE_URL") or (
        f"postgresql://{os.environ.get('PGUSER', 'luthien')}"
        f":{os.environ.get('PGPASSWORD', 'luthien')}"
        f"@{os.environ.get('PGHOST', 'localhost')}"
        f":{os.environ.get('PGPORT', '5432')}"
        f"/{os.environ.get('PGDATABASE', 'luthien_control')}"
    )
    conn = await asyncpg.connect(dsn)

    pg_dir = MIGRATIONS_ROOT / "postgres"
    for mf in sorted(pg_dir.glob("*.sql")):
        # 000 creates users/databases requiring superuser — skip in test
        if mf.name.startswith("000"):
            continue
        sql = mf.read_text()
        await conn.execute(sql)

    # Extract tables and columns (exclude system tables)
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
    )

    tables = {}
    for row in rows:
        table_name = row["table_name"]
        cols = await conn.fetch(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1 "
            "ORDER BY ordinal_position",
            table_name,
        )
        tables[table_name] = {
            "columns": {
                col["column_name"]: {
                    "type": normalize_pg_type(col["data_type"]),
                    "notnull": col["is_nullable"] == "NO",
                }
                for col in cols
            }
        }

    await conn.close()
    return tables


@pytest.mark.integration
class TestMigrationSync:
    """Verify SQLite and Postgres migrations produce equivalent schemas."""

    @pytest.mark.asyncio
    async def test_schemas_match(self) -> None:
        """Table names and column definitions should match across dialects."""
        sqlite_schema = get_sqlite_schema()
        pg_schema = await get_postgres_schema()

        # Compare table sets (exclude _migrations tracking table)
        sqlite_tables = {t for t in sqlite_schema if t != "_migrations"}
        pg_tables = {t for t in pg_schema if t != "_migrations"}

        assert sqlite_tables == pg_tables, (
            f"Table mismatch.\n  SQLite only: {sqlite_tables - pg_tables}\n  Postgres only: {pg_tables - sqlite_tables}"
        )

        # Compare columns per table
        for table in sorted(sqlite_tables):
            sqlite_cols = sqlite_schema[table]["columns"]
            pg_cols = pg_schema[table]["columns"]

            assert set(sqlite_cols.keys()) == set(pg_cols.keys()), (
                f"Column mismatch in '{table}'.\n"
                f"  SQLite only: {set(sqlite_cols.keys()) - set(pg_cols.keys())}\n"
                f"  Postgres only: {set(pg_cols.keys()) - set(sqlite_cols.keys())}"
            )

            for col_name in sqlite_cols:
                sqlite_type = sqlite_cols[col_name]["type"]
                pg_type = pg_cols[col_name]["type"]
                assert sqlite_type == pg_type, (
                    f"Type mismatch in '{table}.{col_name}': SQLite={sqlite_type}, Postgres(normalized)={pg_type}"
                )
