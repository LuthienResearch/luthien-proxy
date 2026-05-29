"""Unit tests for the history user_labels service (real in-memory SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from luthien_proxy.history import user_labels as ul
from luthien_proxy.observability.session_summary import update_session_summary
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations


@pytest.fixture
async def pool() -> DatabasePool:
    p = DatabasePool("sqlite://:memory:")
    await check_migrations(p)
    return p


async def _seed_user_session(pool: DatabasePool, session_id: str, user_id: str) -> None:
    async with pool.connection() as conn:
        await update_session_summary(
            conn,
            session_id=session_id,
            event_type="transaction.request_recorded",
            data={"final_model": "m", "final_request": {"messages": [{"role": "user", "content": "hi"}]}},
            user_id=user_id,
            timestamp=datetime.now(UTC),
        )


class TestSetLabel:
    async def test_set_and_list(self, pool: DatabasePool) -> None:
        name = await ul.set_label(pool, "alice", "Alice")
        assert name == "Alice"
        assert await ul.list_labels(pool) == {"alice": "Alice"}

    async def test_set_strips_whitespace(self, pool: DatabasePool) -> None:
        name = await ul.set_label(pool, "alice", "  Alice Smith  ")
        assert name == "Alice Smith"

    async def test_set_blank_raises(self, pool: DatabasePool) -> None:
        with pytest.raises(ValueError):
            await ul.set_label(pool, "alice", "   ")

    async def test_set_updates_existing(self, pool: DatabasePool) -> None:
        await ul.set_label(pool, "alice", "Alice")
        await ul.set_label(pool, "alice", "Alicia")
        assert await ul.list_labels(pool) == {"alice": "Alicia"}


class TestDeleteLabel:
    async def test_delete_removes(self, pool: DatabasePool) -> None:
        await ul.set_label(pool, "alice", "Alice")
        await ul.delete_label(pool, "alice")
        assert await ul.list_labels(pool) == {}

    async def test_delete_missing_is_noop(self, pool: DatabasePool) -> None:
        await ul.delete_label(pool, "nobody")  # must not raise
        assert await ul.list_labels(pool) == {}


class TestListUsers:
    async def test_lists_distinct_users_with_labels(self, pool: DatabasePool) -> None:
        await _seed_user_session(pool, "s1", "alice")
        await _seed_user_session(pool, "s2", "bob")
        await _seed_user_session(pool, "s3", "alice")  # duplicate user
        await ul.set_label(pool, "alice", "Alice")

        result = await ul.list_users(pool)
        assert result["users"] == ["alice", "bob"]
        assert result["labels"] == {"alice": "Alice"}

    async def test_empty(self, pool: DatabasePool) -> None:
        result = await ul.list_users(pool)
        assert result == {"users": [], "labels": {}}

    async def test_pagination(self, pool: DatabasePool) -> None:
        for i in range(5):
            await _seed_user_session(pool, f"s{i}", f"user{i}")
        page = await ul.list_users(pool, limit=2, offset=0)
        assert len(page["users"]) == 2
        page2 = await ul.list_users(pool, limit=2, offset=2)
        assert len(page2["users"]) == 2
        assert set(page["users"]).isdisjoint(set(page2["users"]))
