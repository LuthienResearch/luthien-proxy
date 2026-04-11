"""Unit tests for the supply-chain blocklist DB layer.

Runs against an in-memory SQLite :class:`DatabasePool` using the canonical
fixture pattern from ``tests/luthien_proxy/unit_tests/utils/test_db.py``.
Every query written by the DB layer is exercised against the real SQLite
translator so any asyncpg-only syntax shows up immediately.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from luthien_proxy.policies.supply_chain_blocklist_db import (
    BlocklistRow,
    get_cursor,
    load_all_entries,
    set_cursor,
    upsert_entries,
)
from luthien_proxy.utils import db


async def _setup_pool() -> db.DatabasePool:
    pool = db.DatabasePool("sqlite://:memory:")
    backing = await pool.get_pool()
    await backing.execute(
        """
        CREATE TABLE supply_chain_blocklist (
            ecosystem      TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            cve_id         TEXT NOT NULL,
            affected_range TEXT NOT NULL,
            severity       TEXT NOT NULL,
            published_at   TEXT NOT NULL,
            fetched_at     TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (ecosystem, canonical_name, cve_id, affected_range)
        )
        """
    )
    await backing.execute(
        """
        CREATE TABLE supply_chain_blocklist_cursor (
            ecosystem     TEXT PRIMARY KEY,
            last_seen_at  TEXT NOT NULL,
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    return pool


def _row(cve: str = "CVE-1", name: str = "litellm") -> BlocklistRow:
    return BlocklistRow(
        ecosystem="PyPI",
        canonical_name=name,
        cve_id=cve,
        affected_range='{"fixed":"1.6.9","introduced":null,"last_affected":null}',
        severity="CRITICAL",
        published_at=datetime(2026, 4, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_load_all_entries_empty() -> None:
    pool = await _setup_pool()
    try:
        assert await load_all_entries(await pool.get_pool()) == []
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_upsert_then_load() -> None:
    pool = await _setup_pool()
    try:
        backing = await pool.get_pool()
        await upsert_entries(backing, [_row(cve="CVE-1"), _row(cve="CVE-2", name="axios")])
        rows = await load_all_entries(backing)
        assert len(rows) == 2
        cve_ids = sorted(r.cve_id for r in rows)
        assert cve_ids == ["CVE-1", "CVE-2"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_upsert_is_idempotent() -> None:
    pool = await _setup_pool()
    try:
        backing = await pool.get_pool()
        await upsert_entries(backing, [_row()])
        await upsert_entries(backing, [_row()])
        rows = await load_all_entries(backing)
        assert len(rows) == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_cursor_set_and_get() -> None:
    pool = await _setup_pool()
    try:
        backing = await pool.get_pool()
        assert await get_cursor(backing, "PyPI") is None
        ts = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
        await set_cursor(backing, "PyPI", ts)
        got = await get_cursor(backing, "PyPI")
        assert got is not None
        assert got.replace(microsecond=0) == ts.replace(microsecond=0)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_cursor_upsert_updates_existing() -> None:
    pool = await _setup_pool()
    try:
        backing = await pool.get_pool()
        t1 = datetime(2026, 4, 1, tzinfo=UTC)
        t2 = datetime(2026, 4, 5, tzinfo=UTC)
        await set_cursor(backing, "PyPI", t1)
        await set_cursor(backing, "PyPI", t2)
        got = await get_cursor(backing, "PyPI")
        assert got is not None
        assert got.replace(microsecond=0) == t2.replace(microsecond=0)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_cursor_per_ecosystem_independent() -> None:
    pool = await _setup_pool()
    try:
        backing = await pool.get_pool()
        t_pypi = datetime(2026, 4, 1, tzinfo=UTC)
        t_npm = datetime(2026, 4, 5, tzinfo=UTC)
        await set_cursor(backing, "PyPI", t_pypi)
        await set_cursor(backing, "npm", t_npm)
        assert (await get_cursor(backing, "PyPI")).replace(microsecond=0) == t_pypi  # type: ignore[union-attr]
        assert (await get_cursor(backing, "npm")).replace(microsecond=0) == t_npm  # type: ignore[union-attr]
    finally:
        await pool.close()
