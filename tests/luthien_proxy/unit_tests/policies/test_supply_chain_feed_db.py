"""Tests for supply_chain_feed_db — DB access layer via in-memory SQLite pool."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from luthien_proxy.policies.supply_chain_feed_db import (
    create_schema,
    get_cursor,
    load_all_entries,
    set_cursor,
    upsert_entries,
)
from luthien_proxy.utils.db import DatabasePool


@pytest.fixture
async def pool():
    """Create an in-memory SQLite pool with schema."""
    db = DatabasePool("sqlite://:memory:")
    p = await db.get_pool()
    await create_schema(p)
    return p


@pytest.mark.asyncio
async def test_create_schema_creates_tables(pool):
    """Schema creation should produce both tables."""
    rows = await pool.fetch(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'supply_chain_feed%'"
    )
    names = {str(r["name"]) for r in rows}
    assert "supply_chain_feed" in names
    assert "supply_chain_feed_cursor" in names


@pytest.mark.asyncio
async def test_upsert_and_load(pool):
    """Upsert entries then load them back."""
    pub = datetime(2025, 1, 1, tzinfo=timezone.utc)
    mod = datetime(2025, 6, 1, tzinfo=timezone.utc)
    entries = [
        ("PyPI", "calibreweb", "0.6.17", "CVE-2022-30765", "CRITICAL", pub, mod),
        ("PyPI", "calibreweb", "0.6.16", "CVE-2022-30765", "CRITICAL", pub, mod),
        ("npm", "axios", "1.6.8", "CVE-2024-39338", "CRITICAL", pub, mod),
    ]
    count = await upsert_entries(pool, entries)
    assert count == 3

    rows = await load_all_entries(pool)
    assert len(rows) == 3

    # Verify data round-trips correctly
    lookup = {(str(r["ecosystem"]), str(r["name"]), str(r["version"])): str(r["cve_id"]) for r in rows}
    assert lookup[("PyPI", "calibreweb", "0.6.17")] == "CVE-2022-30765"
    assert lookup[("npm", "axios", "1.6.8")] == "CVE-2024-39338"


@pytest.mark.asyncio
async def test_upsert_empty(pool):
    """Upserting empty list returns 0."""
    count = await upsert_entries(pool, [])
    assert count == 0


@pytest.mark.asyncio
async def test_upsert_deduplication(pool):
    """Upserting same key twice should not create duplicates."""
    pub = datetime(2025, 1, 1, tzinfo=timezone.utc)
    mod1 = datetime(2025, 6, 1, tzinfo=timezone.utc)
    mod2 = datetime(2025, 7, 1, tzinfo=timezone.utc)

    entries1 = [("PyPI", "calibreweb", "0.6.17", "CVE-2022-30765", "CRITICAL", pub, mod1)]
    entries2 = [("PyPI", "calibreweb", "0.6.17", "CVE-2022-30765", "CRITICAL", pub, mod2)]

    await upsert_entries(pool, entries1)
    await upsert_entries(pool, entries2)

    rows = await load_all_entries(pool)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_cursor_get_set(pool):
    """Cursor get/set round-trip."""
    # No cursor initially
    cursor = await get_cursor(pool, "PyPI")
    assert cursor is None

    ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    await set_cursor(pool, "PyPI", ts)

    cursor = await get_cursor(pool, "PyPI")
    assert cursor is not None
    assert cursor.year == 2025
    assert cursor.month == 6


@pytest.mark.asyncio
async def test_cursor_update(pool):
    """Setting cursor twice updates the value."""
    ts1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    ts2 = datetime(2025, 12, 1, tzinfo=timezone.utc)

    await set_cursor(pool, "PyPI", ts1)
    await set_cursor(pool, "PyPI", ts2)

    cursor = await get_cursor(pool, "PyPI")
    assert cursor is not None
    assert cursor.month == 12


@pytest.mark.asyncio
async def test_multiple_ecosystems(pool):
    """Cursors are independent per ecosystem."""
    ts1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    ts2 = datetime(2025, 6, 1, tzinfo=timezone.utc)

    await set_cursor(pool, "PyPI", ts1)
    await set_cursor(pool, "npm", ts2)

    c1 = await get_cursor(pool, "PyPI")
    c2 = await get_cursor(pool, "npm")
    assert c1 is not None and c1.month == 1
    assert c2 is not None and c2.month == 6
