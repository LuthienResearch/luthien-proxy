"""Validates that database migrations are in sync with local migration files."""

import hashlib
import logging
import os
from pathlib import Path

import asyncpg

from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)

# Default path inside Docker container; can be overridden for local dev
DEFAULT_MIGRATIONS_DIR = "/app/migrations"


def compute_file_hash(filepath: Path) -> str:
    """Compute MD5 hash of a file, matching run-migrations.sh behavior."""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


async def check_migrations(
    db_pool: DatabasePool,
    migrations_dir: str | None = None,
) -> None:
    """Check that all local migrations have been applied to the database.

    Raises RuntimeError if:
    - Local migration files exist that aren't in the database (unapplied migrations)
    - Database has migrations that don't exist locally (stale code)
    - Applied migration hash doesn't match local file (modified migration)

    Args:
        db_pool: Database connection pool
        migrations_dir: Path to migrations directory. Defaults to /app/migrations.
    """
    if migrations_dir is None:
        migrations_dir = os.environ.get("MIGRATIONS_DIR", DEFAULT_MIGRATIONS_DIR)

    migrations_path = Path(migrations_dir)

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
