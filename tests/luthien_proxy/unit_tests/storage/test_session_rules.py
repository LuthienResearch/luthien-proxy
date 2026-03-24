"""Unit tests for session_rules storage module."""

from __future__ import annotations

import pytest

from luthien_proxy.storage.session_rules import SessionRule


class TestSessionRule:
    """Tests for the SessionRule dataclass."""

    def test_creation(self):
        rule = SessionRule(name="no-jargon", instruction="Remove jargon")
        assert rule.name == "no-jargon"
        assert rule.instruction == "Remove jargon"

    def test_frozen(self):
        rule = SessionRule(name="test", instruction="test instruction")
        with pytest.raises(AttributeError):
            rule.name = "changed"  # type: ignore[misc]

    def test_equality(self):
        r1 = SessionRule(name="a", instruction="b")
        r2 = SessionRule(name="a", instruction="b")
        assert r1 == r2

    def test_inequality(self):
        r1 = SessionRule(name="a", instruction="b")
        r2 = SessionRule(name="a", instruction="c")
        assert r1 != r2


class TestSaveAndLoadRules:
    """Tests for save_rules and load_rules using mock db_pool."""

    @pytest.fixture
    def mock_db_pool(self):
        """Create a mock db pool that stores data in memory."""
        import uuid

        class MockConnection:
            def __init__(self, storage):
                self._storage = storage

            async def execute(self, query, *args):
                if "INSERT INTO session_rules" in query:
                    if "?" in query:
                        # SQLite style
                        rule_id, session_id, rule_name, rule_instruction = args
                    else:
                        # PG style
                        session_id, rule_name, rule_instruction = args
                        rule_id = str(uuid.uuid4())
                    self._storage.append({
                        "id": rule_id,
                        "session_id": session_id,
                        "rule_name": rule_name,
                        "rule_instruction": rule_instruction,
                    })

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockTransaction:
            async def __aenter__(self):
                pass
            async def __aexit__(self, *args):
                pass

        class MockPool:
            def __init__(self, storage):
                self._storage = storage

            def acquire(self):
                conn = MockConnection(self._storage)
                conn.transaction = lambda: MockTransaction()
                return conn

            async def fetch(self, query, *args):
                session_id = args[0]
                return [
                    {"rule_name": r["rule_name"], "rule_instruction": r["rule_instruction"]}
                    for r in self._storage
                    if r["session_id"] == session_id
                ]

            async def fetchrow(self, query, *args):
                session_id = args[0]
                matches = [r for r in self._storage if r["session_id"] == session_id]
                return {"1": 1} if matches else None

        class MockDBPool:
            def __init__(self):
                self._storage = []
                self.is_sqlite = True

            async def get_pool(self):
                return MockPool(self._storage)

        return MockDBPool()

    @pytest.mark.asyncio
    async def test_save_and_load_rules(self, mock_db_pool):
        from luthien_proxy.storage.session_rules import load_rules, save_rules

        rules = [
            SessionRule(name="concise", instruction="Be concise"),
            SessionRule(name="no-jargon", instruction="Remove jargon"),
        ]
        await save_rules(mock_db_pool, "session-123", rules)
        loaded = await load_rules(mock_db_pool, "session-123")
        assert len(loaded) == 2
        assert loaded[0].name == "concise"
        assert loaded[1].name == "no-jargon"

    @pytest.mark.asyncio
    async def test_save_empty_rules(self, mock_db_pool):
        from luthien_proxy.storage.session_rules import load_rules, save_rules

        await save_rules(mock_db_pool, "session-123", [])
        loaded = await load_rules(mock_db_pool, "session-123")
        assert loaded == []

    @pytest.mark.asyncio
    async def test_has_rules_true(self, mock_db_pool):
        from luthien_proxy.storage.session_rules import has_rules, save_rules

        rules = [SessionRule(name="test", instruction="test")]
        await save_rules(mock_db_pool, "session-123", rules)
        assert await has_rules(mock_db_pool, "session-123") is True

    @pytest.mark.asyncio
    async def test_has_rules_false(self, mock_db_pool):
        from luthien_proxy.storage.session_rules import has_rules

        assert await has_rules(mock_db_pool, "no-such-session") is False

    @pytest.mark.asyncio
    async def test_load_rules_different_sessions(self, mock_db_pool):
        from luthien_proxy.storage.session_rules import load_rules, save_rules

        await save_rules(mock_db_pool, "session-a", [SessionRule(name="r1", instruction="i1")])
        await save_rules(mock_db_pool, "session-b", [SessionRule(name="r2", instruction="i2")])

        a_rules = await load_rules(mock_db_pool, "session-a")
        b_rules = await load_rules(mock_db_pool, "session-b")

        assert len(a_rules) == 1
        assert a_rules[0].name == "r1"
        assert len(b_rules) == 1
        assert b_rules[0].name == "r2"
