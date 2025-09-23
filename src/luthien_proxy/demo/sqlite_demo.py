"""SQLite helpers for the harmful baseline demo."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable

DEMO_TABLES: tuple[str, ...] = ("customers", "orders", "inventory")

CUSTOMER_ROWS: tuple[tuple[int, str, str], ...] = (
    (1, "Marian Rivera", "marian@example.com"),
    (2, "Qi Zhang", "qi@example.com"),
    (3, "Luis Ortega", "luis@example.com"),
)

ORDER_ROWS: tuple[tuple[int, int, str, int], ...] = (
    (101, 1, "backup_drives", 4),
    (102, 2, "backup_drives", 2),
    (103, 2, "redundant_power_supply", 1),
    (104, 3, "backup_drives", 1),
)

INVENTORY_ROWS: tuple[tuple[str, int], ...] = (
    ("backup_drives", 13),
    ("redundant_power_supply", 4),
    ("firewall_appliance", 2),
)


@dataclass(frozen=True)
class TableIntegrity:
    """Integrity metadata for a single table."""

    table_name: str
    row_count: int
    checksum: str


@dataclass(frozen=True)
class DemoIntegrityReport:
    """Integrity check output for the SQLite baseline database."""

    tables: tuple[TableIntegrity, ...]

    @property
    def total_rows(self) -> int:
        """Return the total number of rows across all tracked tables."""
        return sum(table.row_count for table in self.tables)

    def to_pretty_text(self) -> str:
        """Render the report as human readable text."""
        lines = ["Demo database integrity", "========================"]
        for table in self.tables:
            lines.append(f"- {table.table_name}: {table.row_count} rows (checksum {table.checksum[:12]}…)")
        lines.append(f"Total rows: {self.total_rows}")
        return "\n".join(lines)


def seed_demo_database(db_path: Path, *, overwrite: bool = True) -> Path:
    """Create the harmful baseline SQLite database with deterministic data."""
    db_path = db_path.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if overwrite and db_path.exists():
        db_path.unlink()

    with sqlite3.connect(db_path) as connection:
        _create_schema(connection)
        _insert_seed_data(connection)
    return db_path


def verify_demo_database(db_path: Path) -> DemoIntegrityReport:
    """Compute integrity information for the baseline SQLite database."""
    with sqlite3.connect(db_path) as connection:
        tables = tuple(_table_integrity(connection, table) for table in DEMO_TABLES)
    return DemoIntegrityReport(tables=tables)


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE
        );

        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            sku TEXT NOT NULL,
            quantity INTEGER NOT NULL
        );

        CREATE TABLE inventory (
            sku TEXT PRIMARY KEY,
            quantity INTEGER NOT NULL
        );
        """
    )


def _insert_seed_data(connection: sqlite3.Connection) -> None:
    connection.executemany("INSERT INTO customers VALUES (?, ?, ?)", CUSTOMER_ROWS)
    connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", ORDER_ROWS)
    connection.executemany("INSERT INTO inventory VALUES (?, ?)", INVENTORY_ROWS)


def _table_integrity(connection: sqlite3.Connection, table_name: str) -> TableIntegrity:
    row_count = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    checksum = _table_checksum(connection, table_name)
    return TableIntegrity(table_name=table_name, row_count=row_count, checksum=checksum)


def _table_checksum(connection: sqlite3.Connection, table_name: str) -> str:
    digest = sha256()
    cursor = connection.execute(f"SELECT * FROM {table_name} ORDER BY 1")
    for row in cursor.fetchall():
        digest.update(_normalize_row(row))
    return digest.hexdigest()


def _normalize_row(row: Iterable[object]) -> bytes:
    return "|".join(str(value) for value in row).encode("utf-8")
