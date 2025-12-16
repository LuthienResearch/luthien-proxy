"""Tests for migration validation logic."""

import hashlib
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.utils.migration_check import check_migrations, compute_file_hash


class TestComputeFileHash:
    """Tests for compute_file_hash function."""

    def test_computes_md5_hash(self, tmp_path: Path) -> None:
        """Should compute correct MD5 hash of file contents."""
        test_file = tmp_path / "test.sql"
        content = b"CREATE TABLE foo (id INT);"
        test_file.write_bytes(content)

        result = compute_file_hash(test_file)

        expected = hashlib.md5(content).hexdigest()
        assert result == expected

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        """Different file contents should produce different hashes."""
        file1 = tmp_path / "file1.sql"
        file2 = tmp_path / "file2.sql"
        file1.write_bytes(b"content one")
        file2.write_bytes(b"content two")

        hash1 = compute_file_hash(file1)
        hash2 = compute_file_hash(file2)

        assert hash1 != hash2

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        """Same file contents should produce same hash."""
        file1 = tmp_path / "file1.sql"
        file2 = tmp_path / "file2.sql"
        content = b"identical content"
        file1.write_bytes(content)
        file2.write_bytes(content)

        hash1 = compute_file_hash(file1)
        hash2 = compute_file_hash(file2)

        assert hash1 == hash2


class TestCheckMigrations:
    """Tests for check_migrations function."""

    @pytest.fixture
    def mock_db_pool(self) -> MagicMock:
        """Create a mock DatabasePool."""
        pool = MagicMock()
        mock_pool_instance = AsyncMock()
        pool.get_pool = AsyncMock(return_value=mock_pool_instance)
        return pool

    @pytest.mark.asyncio
    async def test_skips_check_if_directory_not_found(
        self, mock_db_pool: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should skip check and log warning if migrations directory doesn't exist."""
        await check_migrations(mock_db_pool, migrations_dir="/nonexistent/path")

        assert "Migrations directory not found" in caplog.text
        mock_db_pool.get_pool.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_check_if_no_migration_files(
        self, mock_db_pool: MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should skip check and log warning if no .sql files found."""
        await check_migrations(mock_db_pool, migrations_dir=str(tmp_path))

        assert "No migration files found" in caplog.text
        mock_db_pool.get_pool.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_when_all_migrations_applied(
        self, mock_db_pool: MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should pass when local migrations match DB migrations."""
        caplog.set_level(logging.INFO)

        # Create local migration files
        migration1 = tmp_path / "001_init.sql"
        migration2 = tmp_path / "002_add_table.sql"
        migration1.write_bytes(b"CREATE TABLE one;")
        migration2.write_bytes(b"CREATE TABLE two;")

        # Mock DB response with matching migrations (no hashes - legacy migrations)
        mock_pool = mock_db_pool.get_pool.return_value
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"filename": "001_init.sql", "content_hash": None},
                {"filename": "002_add_table.sql", "content_hash": None},
            ]
        )

        await check_migrations(mock_db_pool, migrations_dir=str(tmp_path))

        assert "Migration check passed: 2 migrations applied" in caplog.text

    @pytest.mark.asyncio
    async def test_fails_on_unapplied_migrations(self, mock_db_pool: MagicMock, tmp_path: Path) -> None:
        """Should raise RuntimeError when local migrations aren't in DB."""
        # Create local migration files
        migration1 = tmp_path / "001_init.sql"
        migration2 = tmp_path / "002_new.sql"
        migration1.write_bytes(b"CREATE TABLE one;")
        migration2.write_bytes(b"CREATE TABLE two;")

        # Mock DB response with only first migration
        mock_pool = mock_db_pool.get_pool.return_value
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"filename": "001_init.sql", "content_hash": None},
            ]
        )

        with pytest.raises(RuntimeError) as exc_info:
            await check_migrations(mock_db_pool, migrations_dir=str(tmp_path))

        assert "UNAPPLIED MIGRATIONS" in str(exc_info.value)
        assert "002_new.sql" in str(exc_info.value)
        assert "docker compose up migrations" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fails_on_missing_local_files(self, mock_db_pool: MagicMock, tmp_path: Path) -> None:
        """Should raise RuntimeError when DB has migrations not present locally."""
        # Create only one local migration
        migration1 = tmp_path / "001_init.sql"
        migration1.write_bytes(b"CREATE TABLE one;")

        # Mock DB response with extra migration
        mock_pool = mock_db_pool.get_pool.return_value
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"filename": "001_init.sql", "content_hash": None},
                {"filename": "002_from_other_branch.sql", "content_hash": None},
            ]
        )

        with pytest.raises(RuntimeError) as exc_info:
            await check_migrations(mock_db_pool, migrations_dir=str(tmp_path))

        assert "MISSING LOCAL FILES" in str(exc_info.value)
        assert "002_from_other_branch.sql" in str(exc_info.value)
        assert "stale code" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fails_on_hash_mismatch(self, mock_db_pool: MagicMock, tmp_path: Path) -> None:
        """Should raise RuntimeError when migration content differs from DB hash."""
        # Create local migration with specific content
        migration1 = tmp_path / "001_init.sql"
        original_content = b"CREATE TABLE one;"
        modified_content = b"CREATE TABLE one_modified;"
        migration1.write_bytes(modified_content)

        # Mock DB response with hash of original content
        original_hash = hashlib.md5(original_content).hexdigest()
        mock_pool = mock_db_pool.get_pool.return_value
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"filename": "001_init.sql", "content_hash": original_hash},
            ]
        )

        with pytest.raises(RuntimeError) as exc_info:
            await check_migrations(mock_db_pool, migrations_dir=str(tmp_path))

        assert "HASH MISMATCH" in str(exc_info.value)
        assert "001_init.sql" in str(exc_info.value)
        assert original_hash in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_skips_hash_check_for_null_hash(
        self, mock_db_pool: MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should not fail on hash mismatch if DB hash is null (legacy migration)."""
        caplog.set_level(logging.INFO)

        # Create local migration
        migration1 = tmp_path / "001_init.sql"
        migration1.write_bytes(b"any content")

        # Mock DB response with null hash (legacy migration applied before hash tracking)
        mock_pool = mock_db_pool.get_pool.return_value
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"filename": "001_init.sql", "content_hash": None},
            ]
        )

        # Should not raise
        await check_migrations(mock_db_pool, migrations_dir=str(tmp_path))

        assert "Migration check passed" in caplog.text

    @pytest.mark.asyncio
    async def test_passes_when_hash_matches(
        self, mock_db_pool: MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should pass when migration content matches DB hash."""
        caplog.set_level(logging.INFO)

        # Create local migration
        migration1 = tmp_path / "001_init.sql"
        content = b"CREATE TABLE one;"
        migration1.write_bytes(content)

        # Mock DB response with matching hash
        content_hash = hashlib.md5(content).hexdigest()
        mock_pool = mock_db_pool.get_pool.return_value
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"filename": "001_init.sql", "content_hash": content_hash},
            ]
        )

        await check_migrations(mock_db_pool, migrations_dir=str(tmp_path))

        assert "Migration check passed" in caplog.text

    @pytest.mark.asyncio
    async def test_reports_multiple_errors(self, mock_db_pool: MagicMock, tmp_path: Path) -> None:
        """Should report all errors in single exception."""
        # Create local migration with wrong content
        migration1 = tmp_path / "001_init.sql"
        migration1.write_bytes(b"modified content")

        # Also create a new unapplied migration
        migration2 = tmp_path / "003_new.sql"
        migration2.write_bytes(b"new migration")

        # Mock DB response with:
        # - 001_init.sql with wrong hash
        # - 002_missing.sql that doesn't exist locally
        wrong_hash = hashlib.md5(b"original content").hexdigest()
        mock_pool = mock_db_pool.get_pool.return_value
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"filename": "001_init.sql", "content_hash": wrong_hash},
                {"filename": "002_missing.sql", "content_hash": None},
            ]
        )

        with pytest.raises(RuntimeError) as exc_info:
            await check_migrations(mock_db_pool, migrations_dir=str(tmp_path))

        error_msg = str(exc_info.value)
        assert "UNAPPLIED MIGRATIONS" in error_msg
        assert "003_new.sql" in error_msg
        assert "MISSING LOCAL FILES" in error_msg
        assert "002_missing.sql" in error_msg
        assert "HASH MISMATCH" in error_msg
        assert "001_init.sql" in error_msg
