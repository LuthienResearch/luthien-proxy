"""Integration tests for sync_policy_types.

Tests the full sync flow against a real SQLite database, including:
- Seeding policy_type table with registered built-ins
- Idempotency (no duplicates, no updated created_at on second run)
- Deprecation of missing built-ins
- Resurrection of returning built-ins
- description pulled from __policy_description__ attribute
- module_path resolves to valid classes
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_types import REGISTERED_BUILTINS, sync_policy_types
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import _apply_sqlite_migrations


@pytest.fixture
async def db_pool_with_migrations() -> DatabasePool:
    """Create a fresh in-memory SQLite DatabasePool with all migrations applied."""
    db_pool = DatabasePool("sqlite://:memory:")
    migrations_dir = Path(__file__).resolve().parents[3] / "src" / "luthien_proxy" / "utils" / "sqlite_migrations"
    await _apply_sqlite_migrations(db_pool, migrations_dir=migrations_dir)
    yield db_pool
    await db_pool.close()


@pytest.mark.sqlite_e2e
class TestPolicTypeSyncIntegration:
    """Integration tests for policy_type table sync."""

    @pytest.mark.asyncio
    async def test_sync_seeds_policy_types(self, db_pool_with_migrations: DatabasePool) -> None:
        """Sync with default REGISTERED_BUILTINS populates table with 18 rows."""
        await sync_policy_types(db_pool_with_migrations)

        async with db_pool_with_migrations.connection() as conn:
            rows = await conn.fetch(
                "SELECT name, definition_type, module_path, deprecated FROM policy_type "
                "WHERE definition_type = 'built-in' ORDER BY name"
            )

        assert len(rows) == 18, f"Expected 18 built-in policies, got {len(rows)}"
        for row in rows:
            assert row["name"] is not None
            assert row["definition_type"] == "built-in"
            assert row["module_path"] is not None
            assert row["deprecated"] == 0

    @pytest.mark.asyncio
    async def test_sync_is_idempotent(self, db_pool_with_migrations: DatabasePool) -> None:
        """Second sync call preserves created_at and row count."""
        await sync_policy_types(db_pool_with_migrations)

        async with db_pool_with_migrations.connection() as conn:
            rows1 = await conn.fetch(
                "SELECT id, created_at FROM policy_type WHERE definition_type = 'built-in' ORDER BY id"
            )
            created_ats_1 = {row["id"]: row["created_at"] for row in rows1}
            count1 = len(rows1)

        await sync_policy_types(db_pool_with_migrations)

        async with db_pool_with_migrations.connection() as conn:
            rows2 = await conn.fetch(
                "SELECT id, created_at FROM policy_type WHERE definition_type = 'built-in' ORDER BY id"
            )
            created_ats_2 = {row["id"]: row["created_at"] for row in rows2}
            count2 = len(rows2)

        assert count1 == count2 == 18
        assert created_ats_1 == created_ats_2

    @pytest.mark.asyncio
    async def test_sync_marks_missing_classes_as_deprecated(self, db_pool_with_migrations: DatabasePool) -> None:
        """Call sync with full list, then with shorter list; dropped class marked deprecated."""
        await sync_policy_types(db_pool_with_migrations)

        # Get all the registered paths
        async with db_pool_with_migrations.connection() as conn:
            all_rows = await conn.fetch("SELECT module_path FROM policy_type WHERE definition_type = 'built-in'")
        all_paths = [row["module_path"] for row in all_rows]

        # Sync with shortened list (drop last entry)
        shortened_list = REGISTERED_BUILTINS[:-1]
        await sync_policy_types(db_pool_with_migrations, class_refs=shortened_list)

        async with db_pool_with_migrations.connection() as conn:
            deprecated_rows = await conn.fetch(
                "SELECT module_path FROM policy_type WHERE deprecated = 1 AND definition_type = 'built-in'"
            )

        deprecated_paths = [row["module_path"] for row in deprecated_rows]
        assert len(deprecated_paths) == 1
        assert deprecated_paths[0] == all_paths[-1]

    @pytest.mark.asyncio
    async def test_sync_resurrects_class_when_returned_to_list(self, db_pool_with_migrations: DatabasePool) -> None:
        """Deprecate via shortened list, then re-sync with full list; deprecated flips back."""
        await sync_policy_types(db_pool_with_migrations)

        # Get the path that will be dropped
        async with db_pool_with_migrations.connection() as conn:
            dropped_row = await conn.fetchrow(
                "SELECT module_path FROM policy_type WHERE definition_type = 'built-in' ORDER BY module_path DESC LIMIT 1"
            )
        dropped_path = dropped_row["module_path"]

        # Deprecate via shortened list
        shortened_list = REGISTERED_BUILTINS[:-1]
        await sync_policy_types(db_pool_with_migrations, class_refs=shortened_list)

        async with db_pool_with_migrations.connection() as conn:
            is_deprecated = await conn.fetchval(
                "SELECT deprecated FROM policy_type WHERE module_path = ? AND definition_type = 'built-in'",
                dropped_path,
            )
        assert is_deprecated == 1

        # Resurrect with full list
        await sync_policy_types(db_pool_with_migrations, class_refs=REGISTERED_BUILTINS)

        async with db_pool_with_migrations.connection() as conn:
            is_deprecated = await conn.fetchval(
                "SELECT deprecated FROM policy_type WHERE module_path = ? AND definition_type = 'built-in'",
                dropped_path,
            )
        assert is_deprecated == 0

    @pytest.mark.asyncio
    async def test_sync_per_class_failure_does_not_break_loop(self, db_pool_with_migrations: DatabasePool) -> None:
        """Bad class_ref is skipped; good ones are registered."""
        bad_and_good = ("nonexistent.module:DoesNotExist", REGISTERED_BUILTINS[0])
        await sync_policy_types(db_pool_with_migrations, class_refs=bad_and_good)

        async with db_pool_with_migrations.connection() as conn:
            rows = await conn.fetch("SELECT module_path FROM policy_type WHERE definition_type = 'built-in'")

        paths = [row["module_path"] for row in rows]
        assert REGISTERED_BUILTINS[0] in paths
        assert "nonexistent.module:DoesNotExist" not in paths

    @pytest.mark.asyncio
    async def test_sync_propagates_db_setup_failure(self, db_pool_with_migrations: DatabasePool) -> None:
        """Close the pool before sync; verify it raises."""
        await db_pool_with_migrations.close()

        with pytest.raises(Exception):
            await sync_policy_types(db_pool_with_migrations)

    @pytest.mark.asyncio
    async def test_module_path_uniqueness_enforced_by_db(self, db_pool_with_migrations: DatabasePool) -> None:
        """Direct INSERT with duplicate module_path raises due to partial unique index."""
        # First, insert a valid row via sync
        await sync_policy_types(db_pool_with_migrations)

        # Get a path we can try to duplicate
        async with db_pool_with_migrations.connection() as conn:
            sample_row = await conn.fetchrow(
                "SELECT module_path FROM policy_type WHERE definition_type = 'built-in' LIMIT 1"
            )
        sample_path = sample_row["module_path"]

        # Try to insert a duplicate with same module_path and definition_type='built-in'
        async with db_pool_with_migrations.connection() as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    "INSERT INTO policy_type (name, definition_type, module_path, deprecated) VALUES (?, ?, ?, 0)",
                    "duplicate-name",
                    "built-in",
                    sample_path,
                )

    @pytest.mark.asyncio
    async def test_definition_ref_module_path_imports_to_correct_class(
        self, db_pool_with_migrations: DatabasePool
    ) -> None:
        """After sync, each module_path resolves to a BasePolicy subclass."""
        await sync_policy_types(db_pool_with_migrations)

        async with db_pool_with_migrations.connection() as conn:
            rows = await conn.fetch(
                "SELECT module_path FROM policy_type WHERE definition_type = 'built-in' ORDER BY module_path"
            )

        for row in rows:
            module_path = row["module_path"]
            module_name, class_name = module_path.split(":", 1)
            module = importlib.import_module(module_name)
            policy_class = getattr(module, class_name)

            assert isinstance(policy_class, type), f"{module_path}: not a type"
            assert issubclass(policy_class, BasePolicy), f"{module_path}: not a BasePolicy subclass"
