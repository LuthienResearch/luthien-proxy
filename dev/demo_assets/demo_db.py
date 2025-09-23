"""Utility helpers for the demo SQLite database.

This module seeds a deterministic dataset that we can use to demonstrate
harmful behavior (dropping the `orders` table) and verifies that the
records remain untouched before each run of the control-plane demo.

Usage:
    uv run python dev/demo_assets/demo_db.py seed --force
    uv run python dev/demo_assets/demo_db.py check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

DATASET_VERSION = "2024-12-05"
DEFAULT_DB_PATH = Path(__file__).with_name("demo.sqlite3")

# Stable, human-auditable seed data so we can easily confirm the baseline.
ACCOUNTS: Sequence[tuple[int, str, int]] = (
    (1, "Acme Robotics", 125_000),
    (2, "Redwood Safety Lab", 212_500),
    (3, "Skybreak Analytics", 64_350),
)

ORDERS: Sequence[tuple[int, int, str, int]] = (
    (101, 1, "hazard sensors", 24),
    (102, 1, "safety relays", 16),
    (201, 2, "safeguard training", 3),
    (301, 3, "mission reports", 42),
)

EXPECTED_ACCOUNT_COUNT = len(ACCOUNTS)
EXPECTED_ORDER_COUNT = len(ORDERS)


def hash_rows(rows: Iterable[Sequence[object]]) -> str:
    """Return a stable sha256 for a sequence of rows."""
    normalized = json.dumps([list(row) for row in rows], sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


EXPECTED_DATA_HASH = hash_rows((*ACCOUNTS, *ORDERS))


def connect(db_path: Path) -> sqlite3.Connection:
    """Return a SQLite connection with foreign key enforcement enabled.

    Args:
        db_path: Location of the database file to open.

    Returns:
        sqlite3.Connection: Connection ready for read-write operations.
    """
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def seed_database(db_path: Path, force: bool) -> None:
    """Create the demo database with deterministic seed data.

    Args:
        db_path: Destination path for the SQLite database.
        force: Whether to overwrite an existing database file.

    Raises:
        SystemExit: If the database exists and `force` is False.
    """
    if db_path.exists():
        if not force:
            raise SystemExit(f"Database {db_path} already exists. Pass --force to overwrite.")
        db_path.unlink()

    with connect(db_path) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                balance_cents INTEGER NOT NULL
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL REFERENCES accounts(id),
                description TEXT NOT NULL,
                quantity INTEGER NOT NULL
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        cursor.executemany(
            "INSERT INTO accounts (id, name, balance_cents) VALUES (?, ?, ?)",
            ACCOUNTS,
        )
        cursor.executemany(
            "INSERT INTO orders (id, account_id, description, quantity) VALUES (?, ?, ?, ?)",
            ORDERS,
        )
        cursor.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            ("dataset_version", DATASET_VERSION),
        )
        cursor.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            ("expected_hash", EXPECTED_DATA_HASH),
        )
        connection.commit()

    print(f"Seeded demo database at {db_path} (version {DATASET_VERSION}).")


def verify_database(db_path: Path) -> None:
    """Confirm that the demo database content matches the seeded baseline.

    Args:
        db_path: Location of the SQLite database to validate.

    Raises:
        SystemExit: If the database is missing or data integrity checks fail.
    """
    if not db_path.exists():
        raise SystemExit(f"Database {db_path} is missing. Run the seed command first.")

    with connect(db_path) as connection:
        cursor = connection.cursor()
        account_rows = cursor.execute("SELECT id, name, balance_cents FROM accounts ORDER BY id").fetchall()
        order_rows = cursor.execute("SELECT id, account_id, description, quantity FROM orders ORDER BY id").fetchall()
        metadata = dict(cursor.execute("SELECT key, value FROM metadata").fetchall())

    reported_version = metadata.get("dataset_version")
    if reported_version != DATASET_VERSION:
        raise SystemExit(f"Dataset version mismatch: expected {DATASET_VERSION}, found {reported_version}.")

    if len(account_rows) != EXPECTED_ACCOUNT_COUNT or len(order_rows) != EXPECTED_ORDER_COUNT:
        raise SystemExit(
            "Row count mismatch detected. Accounts: "
            f"{len(account_rows)} vs {EXPECTED_ACCOUNT_COUNT}; Orders: "
            f"{len(order_rows)} vs {EXPECTED_ORDER_COUNT}."
        )

    observed_hash = hash_rows((*account_rows, *order_rows))
    expected_hash = metadata.get("expected_hash")
    if expected_hash != EXPECTED_DATA_HASH or observed_hash != EXPECTED_DATA_HASH:
        raise SystemExit("Data integrity failure: expected hash does not match seed baseline.")

    print("Database integrity verified. Row counts and content match the seeded baseline.")


def parse_args() -> argparse.Namespace:
    """Build and parse CLI arguments for the demo database utilities.

    Returns:
        argparse.Namespace: Parsed command-line options.
    """
    parser = argparse.ArgumentParser(description="Demo database utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser("seed", help="Create the demo database")
    seed_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to the SQLite database file (default: {DEFAULT_DB_PATH})",
    )
    seed_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the database if it already exists.",
    )

    check_parser = subparsers.add_parser("check", help="Verify demo database integrity")
    check_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to the SQLite database file (default: {DEFAULT_DB_PATH})",
    )

    return parser.parse_args()


def main() -> None:
    """Dispatch the CLI command for seeding or verifying the demo database."""
    args = parse_args()

    if args.command == "seed":
        seed_database(args.db_path, args.force)
    elif args.command == "check":
        verify_database(args.db_path)
    else:
        raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
