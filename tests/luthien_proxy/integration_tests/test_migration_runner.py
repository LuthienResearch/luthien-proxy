# ABOUTME: Integration tests for docker/run-migrations.sh's failure handling
# ABOUTME: Pins two fixes: ON_ERROR_STOP=1 on every psql call, and detection of
# ABOUTME: indexes left INVALID by an interrupted CREATE INDEX CONCURRENTLY build

"""Pin `docker/run-migrations.sh`'s error-handling behavior against a real Postgres.

Covers two defects fixed together:

1. `psql -f` without `-v ON_ERROR_STOP=1` prints a failing statement's error,
   continues on to later statements in the same file, and still exits 0 -- so
   a half-failed migration would get recorded as applied.
2. `CREATE INDEX CONCURRENTLY IF NOT EXISTS` matches an existing index by name
   only. A previously interrupted concurrent build leaves an INVALID index
   under that name; a retried deploy's `IF NOT EXISTS` silently "succeeds"
   (exit 0) without the index ever becoming valid. This specifically
   threatens PR #811's `idx_request_logs_created_at` index.

Each test spins up its own scratch Postgres database (dropped afterward) so
tests can run concurrently and don't depend on or pollute the shared
`_migrations` state used elsewhere in the suite.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_MIGRATIONS_SCRIPT = REPO_ROOT / "docker" / "run-migrations.sh"


def _pg_env() -> dict[str, str]:
    return {
        "PGHOST": os.environ.get("PGHOST", "localhost"),
        "PGPORT": os.environ.get("PGPORT", "5432"),
        "PGUSER": os.environ.get("PGUSER", "luthien"),
        "PGPASSWORD": os.environ.get("PGPASSWORD", "luthien"),
    }


def _dsn(database: str) -> str:
    env = _pg_env()
    return f"postgresql://{env['PGUSER']}:{env['PGPASSWORD']}@{env['PGHOST']}:{env['PGPORT']}/{database}"


class ScratchDb:
    """A throwaway Postgres database plus the env vars run-migrations.sh needs to reach it."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.dsn = _dsn(name)
        self.env = {**_pg_env(), "PGDATABASE": name}

    async def connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.dsn)

    async def migration_recorded(self, filename: str) -> bool:
        conn = await self.connect()
        try:
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '_migrations')"
            )
            if not table_exists:
                return False
            count = await conn.fetchval("SELECT COUNT(*) FROM _migrations WHERE filename = $1", filename)
            return bool(count)
        finally:
            await conn.close()


@pytest.fixture
async def scratch_db() -> AsyncIterator[ScratchDb]:
    """Create an isolated scratch database on the shared Postgres instance and drop it afterward."""
    admin_conn = await asyncpg.connect(_dsn(os.environ.get("PGDATABASE", "luthien_control")))
    db_name = f"migration_runner_test_{uuid.uuid4().hex[:16]}"
    await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
    try:
        yield ScratchDb(db_name)
    finally:
        # A killed/failed CONCURRENTLY build can leave connections lingering;
        # terminate them or the DROP below hangs.
        await admin_conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            db_name,
        )
        await admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await admin_conn.close()


def run_migrations(migrations_dir: Path, db: ScratchDb) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **db.env, "MIGRATIONS_DIR": str(migrations_dir)}
    return subprocess.run(
        [str(RUN_MIGRATIONS_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )


async def _plant_invalid_concurrent_index(db: ScratchDb, index_name: str) -> None:
    """Leave an INVALID index named `index_name` behind, deterministically.

    A `CREATE UNIQUE INDEX CONCURRENTLY` over duplicate data always fails with
    a real Postgres error during its validation scan and leaves the partially
    built index catalogued as invalid -- this reproduces the same on-disk
    state a killed/interrupted concurrent build would, without any timing- or
    process-kill-dependent race.
    """
    conn = await db.connect()
    try:
        await conn.execute("CREATE TABLE dup_source (id serial primary key, val int)")
        await conn.execute("INSERT INTO dup_source (val) VALUES (1), (1)")
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(f"CREATE UNIQUE INDEX CONCURRENTLY {index_name} ON dup_source (val)")
        is_valid = await conn.fetchval(
            "SELECT indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = $1",
            index_name,
        )
        assert is_valid is False, "test setup did not produce an invalid index"
    finally:
        await conn.close()


async def test_bad_statement_aborts_and_is_not_recorded(scratch_db: ScratchDb, tmp_path: Path) -> None:
    """A failing statement must abort the run and leave the migration unrecorded."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "900_bad.sql").write_text(
        "CREATE TABLE scratch_demo (id INT PRIMARY KEY);\n"
        "INSERT INTO scratch_demo (id) VALUES (1);\n"
        "SELEKT * FROM nonexistent_table;\n"
    )

    result = run_migrations(migrations_dir, scratch_db)

    assert result.returncode != 0, result.stdout + result.stderr
    assert not await scratch_db.migration_recorded("900_bad.sql")


async def test_bad_statement_stops_before_later_statements_in_same_file(scratch_db: ScratchDb, tmp_path: Path) -> None:
    """Statements after a failing one in the same file must never execute."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "900_bad.sql").write_text(
        "CREATE TABLE scratch_demo (id INT PRIMARY KEY);\n"
        "INSERT INTO scratch_demo (id) VALUES (1);\n"
        "SELEKT * FROM nonexistent_table;\n"
        "INSERT INTO scratch_demo (id) VALUES (2);\n"
    )

    run_migrations(migrations_dir, scratch_db)

    conn = await scratch_db.connect()
    try:
        rows = await conn.fetch("SELECT id FROM scratch_demo ORDER BY id")
    finally:
        await conn.close()
    assert [row["id"] for row in rows] == [1]


async def test_multiple_migration_files_stop_at_first_failure(scratch_db: ScratchDb, tmp_path: Path) -> None:
    """A failure in one file must prevent later files from being attempted at all."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_good.sql").write_text("CREATE TABLE already_applied (id INT);\n")
    (migrations_dir / "002_bad.sql").write_text("SELEKT bogus;\n")
    (migrations_dir / "003_good.sql").write_text("CREATE TABLE never_reached (id INT);\n")

    result = run_migrations(migrations_dir, scratch_db)

    assert result.returncode != 0
    assert await scratch_db.migration_recorded("001_good.sql")
    assert not await scratch_db.migration_recorded("002_bad.sql")
    assert not await scratch_db.migration_recorded("003_good.sql")

    conn = await scratch_db.connect()
    try:
        never_reached_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'never_reached')"
        )
    finally:
        await conn.close()
    assert never_reached_exists is False


async def test_valid_migration_is_recorded_with_correct_content_hash(scratch_db: ScratchDb, tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration_file = migrations_dir / "001_good.sql"
    migration_file.write_text("CREATE TABLE ok_table (id INT);\n")

    result = run_migrations(migrations_dir, scratch_db)

    assert result.returncode == 0, result.stdout + result.stderr
    expected_hash = hashlib.md5(migration_file.read_bytes()).hexdigest()  # noqa: S324 - matches run-migrations.sh's own hash choice

    conn = await scratch_db.connect()
    try:
        row = await conn.fetchrow("SELECT content_hash FROM _migrations WHERE filename = $1", "001_good.sql")
    finally:
        await conn.close()
    assert row is not None
    assert row["content_hash"] == expected_hash


async def test_already_applied_migration_is_skipped_and_not_reapplied(scratch_db: ScratchDb, tmp_path: Path) -> None:
    """A second run must not re-execute an already-applied (non-idempotent) migration."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_seed.sql").write_text(
        "CREATE TABLE seen (id serial primary key);\nINSERT INTO seen DEFAULT VALUES;\n"
    )

    first = run_migrations(migrations_dir, scratch_db)
    assert first.returncode == 0, first.stdout + first.stderr

    second = run_migrations(migrations_dir, scratch_db)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Skipping (already applied): 001_seed.sql" in second.stdout

    conn = await scratch_db.connect()
    try:
        count = await conn.fetchval("SELECT COUNT(*) FROM seen")
    finally:
        await conn.close()
    assert count == 1


async def test_validation_phase_rejects_missing_local_file(scratch_db: ScratchDb, tmp_path: Path) -> None:
    """Pre-existing drift check: a migration recorded in the DB but missing locally must fail fast."""
    first_dir = tmp_path / "migrations_v1"
    first_dir.mkdir()
    (first_dir / "001_only_here_first.sql").write_text("CREATE TABLE t1 (id INT);\n")
    first = run_migrations(first_dir, scratch_db)
    assert first.returncode == 0, first.stdout + first.stderr

    second_dir = tmp_path / "migrations_v2"
    second_dir.mkdir()
    result = run_migrations(second_dir, scratch_db)

    assert result.returncode != 0
    assert "file not found locally" in result.stdout


async def test_validation_phase_rejects_content_hash_mismatch(scratch_db: ScratchDb, tmp_path: Path) -> None:
    """Pre-existing drift check: editing an already-applied migration file must fail fast."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration_file = migrations_dir / "001_mutable.sql"
    migration_file.write_text("CREATE TABLE t1 (id INT);\n")
    first = run_migrations(migrations_dir, scratch_db)
    assert first.returncode == 0, first.stdout + first.stderr

    migration_file.write_text("CREATE TABLE t1 (id INT); -- modified after being applied\n")
    result = run_migrations(migrations_dir, scratch_db)

    assert result.returncode != 0
    assert "Content mismatch" in result.stdout


async def test_preexisting_invalid_concurrent_index_blocks_recording(scratch_db: ScratchDb, tmp_path: Path) -> None:
    """The retry trap: IF NOT EXISTS must not let a previously-invalid index pass as applied."""
    await _plant_invalid_concurrent_index(scratch_db, "idx_dup_source_val")

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_add_index.sql").write_text(
        "-- mirrors PR #811's CREATE INDEX CONCURRENTLY IF NOT EXISTS shape\n"
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_dup_source_val ON dup_source (val);\n"
    )

    result = run_migrations(migrations_dir, scratch_db)

    assert result.returncode != 0, "the old script exited 0 here; the invalid index must now block success"
    assert "is not valid" in result.stdout
    assert not await scratch_db.migration_recorded("001_add_index.sql")


async def test_valid_concurrent_index_build_is_recorded_normally(scratch_db: ScratchDb, tmp_path: Path) -> None:
    """No false positives: an uninterrupted CONCURRENTLY build still applies and is recorded."""
    conn = await scratch_db.connect()
    try:
        await conn.execute("CREATE TABLE clean_source (id serial primary key, val int)")
        await conn.execute("INSERT INTO clean_source (val) SELECT g FROM generate_series(1, 100) g")
    finally:
        await conn.close()

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_add_index.sql").write_text(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_clean_source_val ON clean_source (val);\n"
    )

    result = run_migrations(migrations_dir, scratch_db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert await scratch_db.migration_recorded("001_add_index.sql")

    conn = await scratch_db.connect()
    try:
        is_valid = await conn.fetchval(
            "SELECT indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = 'idx_clean_source_val'"
        )
    finally:
        await conn.close()
    assert is_valid is True


async def test_concurrent_index_scan_ignores_sql_comments(scratch_db: ScratchDb, tmp_path: Path) -> None:
    """A comment mentioning 'create index concurrently' must not be parsed as a real index name."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_commented.sql").write_text(
        "-- note: this comment mentions create index concurrently in passing\n"
        "CREATE TABLE commented_table (id INT); -- trailing comment: create index concurrently too\n"
    )

    result = run_migrations(migrations_dir, scratch_db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert await scratch_db.migration_recorded("001_commented.sql")


async def test_migration_without_concurrent_index_is_unaffected_by_index_check(
    scratch_db: ScratchDb, tmp_path: Path
) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_plain_index.sql").write_text(
        "CREATE TABLE plain_table (id serial primary key, val int);\n"
        "CREATE INDEX idx_plain_table_val ON plain_table (val);\n"
    )

    result = run_migrations(migrations_dir, scratch_db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert await scratch_db.migration_recorded("001_plain_index.sql")


async def test_full_repository_migration_set_applies_cleanly(scratch_db: ScratchDb) -> None:
    """Regression guard: the real migrations/postgres set must still apply end to end."""
    result = run_migrations(REPO_ROOT / "migrations", scratch_db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "🎉 All migrations complete!" in result.stdout
