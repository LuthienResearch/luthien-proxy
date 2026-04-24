"""Regression tests for CredentialStore against a real SQLite backend.

These tests catch SQL-level bugs the mock-based tests in `test_store.py`
can't surface — notably the positional-arg-reuse bug that broke
`POST /api/admin/credentials` on SQLite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.credentials.store import CredentialStore
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations


@pytest.fixture
async def sqlite_db_pool() -> DatabasePool:
    """In-memory SQLite DatabasePool with all migrations applied.

    Runs the real migration runner (`check_migrations`) instead of an inlined
    CREATE TABLE so schema drift between migrations/sqlite/*.sql and this test
    fails loudly here instead of silently passing while production breaks.
    """
    db = DatabasePool("sqlite://:memory:")
    await db.get_pool()  # eagerly open so check_migrations sees the same pool
    await check_migrations(db)
    return db


@pytest.mark.asyncio
async def test_put_then_get_roundtrip_against_real_sqlite(sqlite_db_pool):
    """put() → get() roundtrip works against real SQLite.

    Regression for the positional-arg-reuse bug (PR #598): the INSERT
    statement in put() uses `VALUES (..., $8, $8)` to write created_at and
    updated_at from one arg. If the SQLite translator doesn't honor
    positional reuse, the execute fails with sqlite3.ProgrammingError.
    """
    store = CredentialStore(sqlite_db_pool)
    credential = Credential(
        value="test-api-key",
        credential_type=CredentialType.API_KEY,
        platform="anthropic",
        platform_url="https://api.anthropic.com",
        expiry=None,
    )

    await store.put("test-key", credential)
    result = await store.get("test-key")

    assert result is not None
    assert result.value == "test-api-key"
    assert result.credential_type == CredentialType.API_KEY
    assert result.platform == "anthropic"
    assert result.platform_url == "https://api.anthropic.com"
    assert result.expiry is None


@pytest.mark.asyncio
async def test_put_upsert_updates_existing_row(sqlite_db_pool):
    """put() on an existing name overwrites the row (ON CONFLICT DO UPDATE)."""
    store = CredentialStore(sqlite_db_pool)
    first = Credential(
        value="first-value",
        credential_type=CredentialType.API_KEY,
        platform="anthropic",
        platform_url=None,
        expiry=None,
    )
    second = Credential(
        value="second-value",
        credential_type=CredentialType.API_KEY,
        platform="openai",
        platform_url="https://api.openai.com",
        expiry=None,
    )

    await store.put("same-name", first)
    await store.put("same-name", second)
    result = await store.get("same-name")

    assert result is not None
    assert result.value == "second-value"
    assert result.platform == "openai"


@pytest.mark.asyncio
async def test_put_with_expiry_and_encryption(sqlite_db_pool):
    """Encrypted credentials with expiry roundtrip correctly through SQLite."""
    key = Fernet.generate_key()
    store = CredentialStore(sqlite_db_pool, encryption_key=key)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    credential = Credential(
        value="secret",
        credential_type=CredentialType.API_KEY,
        platform="anthropic",
        platform_url=None,
        expiry=future,
    )

    await store.put("encrypted", credential)
    result = await store.get("encrypted")

    assert result is not None
    assert result.value == "secret"
    assert result.expiry is not None
    assert result.expiry.tzinfo is not None


@pytest.mark.asyncio
async def test_delete_and_list(sqlite_db_pool):
    """delete() removes rows; list_names() reflects current state."""
    store = CredentialStore(sqlite_db_pool)
    for name in ("alpha", "beta", "gamma"):
        await store.put(
            name,
            Credential(
                value=f"value-{name}",
                credential_type=CredentialType.API_KEY,
                platform="anthropic",
                platform_url=None,
                expiry=None,
            ),
        )

    assert await store.list_names() == ["alpha", "beta", "gamma"]
    assert await store.delete("beta") is True
    assert await store.delete("beta") is False
    assert await store.list_names() == ["alpha", "gamma"]
