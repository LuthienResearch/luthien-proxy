"""Unit tests for session rules storage."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.storage.session_rules import (
    SENTINEL_RULE_NAME,
    SessionRule,
    has_rules,
    load_rules,
    save_rules,
)


class TestSessionRuleDataclass:
    def test_frozen(self):
        rule = SessionRule(name="test", instruction="Do the thing")
        assert rule.name == "test"
        assert rule.instruction == "Do the thing"
        with pytest.raises(AttributeError):
            rule.name = "changed"  # type: ignore[misc]


def _make_mock_db_pool(*, is_sqlite: bool = True) -> MagicMock:
    """Create a mock DatabasePool with connection context manager and pool fetch methods."""
    mock_conn = AsyncMock()

    @asynccontextmanager
    async def _mock_transaction():
        yield

    mock_conn.transaction = _mock_transaction

    pool = MagicMock()
    pool.is_sqlite = is_sqlite

    @asynccontextmanager
    async def _mock_connection():
        yield mock_conn

    pool.connection = _mock_connection
    pool._mock_conn = mock_conn  # expose for assertions

    mock_pool_obj = AsyncMock()
    pool.get_pool = AsyncMock(return_value=mock_pool_obj)

    return pool


class TestSaveRules:
    @pytest.mark.asyncio
    async def test_save_rules_inserts_rows(self):
        db_pool = _make_mock_db_pool(is_sqlite=True)
        rules = [
            SessionRule(name="r1", instruction="Rule 1"),
            SessionRule(name="r2", instruction="Rule 2"),
        ]

        await save_rules(db_pool, "session-1", rules)

        mock_conn = db_pool._mock_conn
        assert mock_conn.execute.call_count == 2

        # Verify the SQL uses ? placeholders for SQLite
        first_call = mock_conn.execute.call_args_list[0]
        assert "?" in first_call.args[0]
        assert first_call.args[2] == "session-1"
        assert first_call.args[3] == "r1"
        assert first_call.args[4] == "Rule 1"

    @pytest.mark.asyncio
    async def test_save_empty_rules_inserts_sentinel(self):
        db_pool = _make_mock_db_pool(is_sqlite=True)

        await save_rules(db_pool, "session-1", [])

        mock_conn = db_pool._mock_conn
        assert mock_conn.execute.call_count == 1

        call = mock_conn.execute.call_args_list[0]
        assert call.args[3] == SENTINEL_RULE_NAME
        assert call.args[4] == ""

    @pytest.mark.asyncio
    async def test_save_rules_postgres_uses_dollar_placeholders(self):
        db_pool = _make_mock_db_pool(is_sqlite=False)
        rules = [SessionRule(name="r1", instruction="Rule 1")]

        await save_rules(db_pool, "session-1", rules)

        mock_conn = db_pool._mock_conn
        first_call = mock_conn.execute.call_args_list[0]
        assert "$1" in first_call.args[0]


class TestLoadRules:
    @pytest.mark.asyncio
    async def test_load_rules_returns_session_rules(self):
        db_pool = _make_mock_db_pool(is_sqlite=True)
        mock_pool_obj = await db_pool.get_pool()
        mock_pool_obj.fetch = AsyncMock(
            return_value=[
                {"rule_name": "r1", "rule_instruction": "Do thing 1"},
                {"rule_name": "r2", "rule_instruction": "Do thing 2"},
            ]
        )

        result = await load_rules(db_pool, "session-1")

        assert len(result) == 2
        assert result[0].name == "r1"
        assert result[0].instruction == "Do thing 1"
        assert result[1].name == "r2"

    @pytest.mark.asyncio
    async def test_load_rules_filters_sentinel(self):
        """load_rules query excludes sentinel rows."""
        db_pool = _make_mock_db_pool(is_sqlite=True)
        mock_pool_obj = await db_pool.get_pool()
        mock_pool_obj.fetch = AsyncMock(return_value=[])

        result = await load_rules(db_pool, "session-1")

        assert result == []
        # Verify the query filters out the sentinel
        call_args = mock_pool_obj.fetch.call_args
        assert SENTINEL_RULE_NAME in call_args.args


class TestHasRules:
    @pytest.mark.asyncio
    async def test_has_rules_true_when_rows_exist(self):
        db_pool = _make_mock_db_pool(is_sqlite=True)
        mock_pool_obj = await db_pool.get_pool()
        mock_pool_obj.fetchrow = AsyncMock(return_value={"1": 1})

        result = await has_rules(db_pool, "session-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_has_rules_false_when_no_rows(self):
        db_pool = _make_mock_db_pool(is_sqlite=True)
        mock_pool_obj = await db_pool.get_pool()
        mock_pool_obj.fetchrow = AsyncMock(return_value=None)

        result = await has_rules(db_pool, "session-1")
        assert result is False
