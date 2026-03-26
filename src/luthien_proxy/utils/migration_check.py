"""Validates that database migrations are in sync with local migration files."""

import hashlib
import logging
import os
import uuid
from pathlib import Path

import asyncpg

from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)

# Default path inside Docker container; can be overridden for local dev
DEFAULT_MIGRATIONS_DIR = "/app/migrations"

SNAPSHOT_ERA_MARKER_TABLE = "current_policy"
BOOTSTRAP_THROUGH_PREFIX = "009"


def compute_file_hash(filepath: Path) -> str:
    """Compute MD5 hash of a file, matching run-migrations.sh behavior."""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _find_sqlite_migrations_dir() -> Path | None:
    """Locate the sqlite migrations directory.

    Checks (in order):
    1. MIGRATIONS_DIR env var + /sqlite/ subdirectory
    2. Bundled with the package (sqlite_migrations/ next to this file)
    3. Repo-relative path (migrations/sqlite/ at repo root)
    4. Relative to the current working directory
    """
    candidates = [
        Path(__file__).resolve().parent / "sqlite_migrations",
        Path(__file__).resolve().parents[3] / "migrations" / "sqlite",
        Path("migrations/sqlite"),
    ]

    migrations_dir = os.environ.get("MIGRATIONS_DIR")
    if migrations_dir:
        candidates.insert(0, Path(migrations_dir) / "sqlite")

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


async def _apply_sqlite_migrations(
    db_pool: DatabasePool,
    migrations_dir: Path | None = None,
) -> None:
    """Apply SQLite migrations incrementally.

    For each .sql file in the migrations directory (sorted by name):
    1. Skip if already recorded in _migrations
    2. Execute the SQL statements
    3. Record filename + content_hash in _migrations

    Handles upgrade from snapshot-era databases by detecting existing tables
    with no migration tracking and seeding _migrations.
    """
    if migrations_dir is None:
        migrations_dir = _find_sqlite_migrations_dir()
    if migrations_dir is None:
        logger.warning("SQLite migrations directory not found -- skipping")
        return

    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        logger.warning(f"No migration files in {migrations_dir} -- skipping")
        return

    async with db_pool.connection() as conn:
        # Ensure _migrations table exists
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "    filename TEXT PRIMARY KEY,"
            "    applied_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "    content_hash TEXT"
            ")"
        )

        # Check for snapshot-era database: _migrations empty but tables exist
        migration_count = await conn.fetchval("SELECT COUNT(*) FROM _migrations")
        if migration_count == 0:
            marker_exists = await conn.fetchval(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                SNAPSHOT_ERA_MARKER_TABLE,
            )
            if marker_exists:
                logger.info("Detected snapshot-era SQLite database -- bootstrapping migration tracking")
                for mf in migration_files:
                    if mf.stem.split("_")[0] <= BOOTSTRAP_THROUGH_PREFIX:
                        content_hash = compute_file_hash(mf)
                        await conn.execute(
                            "INSERT OR IGNORE INTO _migrations (filename, content_hash) VALUES (?, ?)",
                            mf.name,
                            content_hash,
                        )
                logger.info("Bootstrap complete -- existing migrations seeded")

        # Get already-applied migrations
        applied_rows = await conn.fetch("SELECT filename, content_hash FROM _migrations ORDER BY filename")
        applied = {row["filename"]: row["content_hash"] for row in applied_rows}

        # Validate + apply
        for mf in migration_files:
            if mf.name in applied:
                # Validate hash
                db_hash = applied[mf.name]
                if db_hash:
                    local_hash = compute_file_hash(mf)
                    if db_hash != local_hash:
                        raise RuntimeError(
                            f"HASH MISMATCH: {mf.name}\n"
                            f"   DB hash:    {db_hash}\n"
                            f"   Local hash: {local_hash}\n"
                            "   Migration file was modified after being applied."
                        )
                continue

            # Apply migration
            sql = mf.read_text()
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement and not all(
                    line.strip().startswith("--") or not line.strip() for line in statement.split("\n")
                ):
                    await conn.execute(statement)

            content_hash = compute_file_hash(mf)
            await conn.execute(
                "INSERT INTO _migrations (filename, content_hash) VALUES (?, ?)",
                mf.name,
                content_hash,
            )
            logger.info(f"Applied SQLite migration: {mf.name}")

        # Handle deployment_id for telemetry_config (only if table exists)
        telemetry_exists = await conn.fetchval(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='telemetry_config'"
        )
        if telemetry_exists:
            await conn.execute(
                "UPDATE telemetry_config SET deployment_id = ? WHERE id = 1 AND deployment_id = 'pending'",
                str(uuid.uuid4()),
            )

    logger.info("SQLite migrations complete")


async def check_migrations(
    db_pool: DatabasePool,
    migrations_dir: str | None = None,
) -> None:
    """Check that all local migrations have been applied to the database.

    For SQLite databases, auto-applies the schema instead of checking
    migration state (SQLite has no Docker migration runner).

    Raises RuntimeError if:
    - Local migration files exist that aren't in the database (unapplied migrations)
    - Database has migrations that don't exist locally (stale code)
    - Applied migration hash doesn't match local file (modified migration)

    Args:
        db_pool: Database connection pool
        migrations_dir: Path to migrations directory. Defaults to /app/migrations.
    """
    if db_pool.is_sqlite:
        await _apply_sqlite_migrations(db_pool)
        return

    if migrations_dir is None:
        migrations_dir = os.environ.get("MIGRATIONS_DIR", DEFAULT_MIGRATIONS_DIR)

    migrations_path = Path(migrations_dir)

    postgres_path = migrations_path / "postgres"
    if postgres_path.exists():
        migrations_path = postgres_path

    if not migrations_path.exists():
        logger.warning(f"Migrations directory not found: {migrations_dir} - skipping check")
        return

    # Get local migration files
    local_migrations = {f.name: f for f in sorted(migrations_path.glob("*.sql"))}

    if not local_migrations:
        logger.warning(f"No migration files found in {migrations_dir} - skipping check")
        return

    # Get applied migrations from database
    pool = await db_pool.get_pool()
    try:
        rows = await pool.fetch("SELECT filename, content_hash FROM _migrations ORDER BY filename")
    except asyncpg.UndefinedTableError:
        raise RuntimeError(
            "Migration tracking table '_migrations' not found.\n"
            "The migrations container may not have run.\n"
            "Run: docker compose up migrations"
        )
    db_migrations: dict[str, str | None] = {
        str(row["filename"]): str(row["content_hash"]) if row["content_hash"] else None for row in rows
    }

    errors: list[str] = []

    local_filenames = set(local_migrations.keys())
    db_filenames = set(db_migrations.keys())

    # Check 1: All local migrations should be in DB
    unapplied = local_filenames - db_filenames
    if unapplied:
        errors.append(
            f"UNAPPLIED MIGRATIONS: {sorted(unapplied)}\n"
            "   The migrations container may not have run.\n"
            "   Run: docker compose up migrations"
        )

    # Check 2: All DB migrations should exist locally
    missing_locally = db_filenames - local_filenames
    if missing_locally:
        errors.append(
            f"MISSING LOCAL FILES: {sorted(missing_locally)}\n"
            "   Database has migrations not present in code.\n"
            "   You may be running stale code, or need to pull latest changes."
        )

    # Check 3: Hash mismatches (only for migrations with recorded hashes)
    for filename, db_hash in db_migrations.items():
        if db_hash and filename in local_migrations:
            local_hash = compute_file_hash(local_migrations[filename])
            if db_hash != local_hash:
                errors.append(
                    f"HASH MISMATCH: {filename}\n"
                    f"   DB hash:    {db_hash}\n"
                    f"   Local hash: {local_hash}\n"
                    "   Migration file was modified after being applied."
                )

    if errors:
        error_msg = "Migration check failed!\n\n" + "\n\n".join(errors)
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    logger.info(f"Migration check passed: {len(db_migrations)} migrations applied")
