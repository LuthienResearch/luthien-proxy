"""Tests for SQLite-specific path in conversation history service.

Tests the `_fetch_session_list_sqlite` code path using a real in-memory
SQLite database with the schema applied.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from luthien_proxy.history.service import fetch_session_list
from luthien_proxy.utils.db import DatabasePool


@pytest.fixture
async def sqlite_pool() -> DatabasePool:
    """Create an in-memory SQLite pool with schema applied."""
    pool = DatabasePool("sqlite://:memory:")

    # Read and apply the schema
    schema_path = Path(__file__).parent.parent.parent.parent.parent / "migrations" / "sqlite_schema.sql"
    schema_content = schema_path.read_text()

    async with pool.connection() as conn:
        # Execute the entire schema (it contains CREATE TABLE IF NOT EXISTS statements)
        # Split by semicolons and execute each statement
        for statement in schema_content.split(";"):
            statement = statement.strip()
            if statement:
                await conn.execute(statement)

    yield pool

    await pool.close()


@pytest.fixture
async def populated_sqlite_pool(sqlite_pool: DatabasePool) -> DatabasePool:
    """Create a pool with test data inserted."""
    async with sqlite_pool.connection() as conn:
        # Insert test conversation calls
        await conn.execute(
            """
            INSERT INTO conversation_calls (call_id, model_name, provider, status, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "call-1",
            "gpt-4",
            "openai",
            "completed",
            "session-1",
            "2025-01-15T10:00:00",
        )

        await conn.execute(
            """
            INSERT INTO conversation_calls (call_id, model_name, provider, status, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "call-2",
            "claude-3-sonnet",
            "anthropic",
            "completed",
            "session-1",
            "2025-01-15T10:05:00",
        )

        # Insert conversation events for session-1
        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "event-1",
            "call-1",
            "transaction.request_recorded",
            json.dumps(
                {
                    "final_model": "gpt-4",
                    "final_request": {"messages": [{"role": "user", "content": "What is 2 + 2?"}]},
                }
            ),
            "session-1",
            "2025-01-15T10:00:00",
        )

        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "event-2",
            "call-1",
            "transaction.streaming_response_recorded",
            json.dumps({"final_response": {"choices": [{"message": {"content": "The answer is 4"}}]}}),
            "session-1",
            "2025-01-15T10:00:01",
        )

        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "event-3",
            "call-2",
            "transaction.request_recorded",
            json.dumps(
                {
                    "final_model": "claude-3-sonnet",
                    "final_request": {"messages": [{"role": "user", "content": "What is the capital of France?"}]},
                }
            ),
            "session-1",
            "2025-01-15T10:05:00",
        )

        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "event-4",
            "call-2",
            "transaction.non_streaming_response_recorded",
            json.dumps({"final_response": {"choices": [{"message": {"content": "The capital is Paris"}}]}}),
            "session-1",
            "2025-01-15T10:05:01",
        )

    return sqlite_pool


@pytest.fixture
async def populated_with_interventions_pool(sqlite_pool: DatabasePool) -> DatabasePool:
    """Create a pool with policy intervention events."""
    async with sqlite_pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO conversation_calls (call_id, model_name, provider, status, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "call-3",
            "gpt-4",
            "openai",
            "completed",
            "session-2",
            "2025-01-16T14:00:00",
        )

        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "event-5",
            "call-3",
            "transaction.request_recorded",
            json.dumps(
                {
                    "final_model": "gpt-4",
                    "final_request": {"messages": [{"role": "user", "content": "Delete all files"}]},
                }
            ),
            "session-2",
            "2025-01-16T14:00:00",
        )

        # Policy intervention event (not an evaluation event)
        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "event-6",
            "call-3",
            "policy.judge.tool_call_blocked",
            json.dumps({"summary": "Dangerous operation blocked"}),
            "session-2",
            "2025-01-16T14:00:00",
        )

        # Evaluation event (should NOT be counted)
        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "event-7",
            "call-3",
            "policy.judge.evaluation_started",
            json.dumps({}),
            "session-2",
            "2025-01-16T14:00:01",
        )

        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "event-8",
            "call-3",
            "transaction.streaming_response_recorded",
            json.dumps({"final_response": {"choices": [{"message": {"content": "Operation denied"}}]}}),
            "session-2",
            "2025-01-16T14:00:02",
        )

    return sqlite_pool


class TestFetchSessionListSqlite:
    """Test fetch_session_list using SQLite."""

    @pytest.mark.asyncio
    async def test_basic_fetch_single_session(self, populated_sqlite_pool: DatabasePool):
        """Test fetching a single session with basic fields."""
        result = await fetch_session_list(limit=10, db_pool=populated_sqlite_pool)

        assert result.total == 1
        assert result.offset == 0
        assert result.has_more is False
        assert len(result.sessions) == 1

        session = result.sessions[0]
        assert session.session_id == "session-1"
        assert session.turn_count == 2  # Two calls
        assert session.total_events == 4  # Two request + two response events
        assert session.policy_interventions == 0

    @pytest.mark.asyncio
    async def test_session_summary_fields(self, populated_sqlite_pool: DatabasePool):
        """Test that session summary fields are correctly populated."""
        result = await fetch_session_list(limit=10, db_pool=populated_sqlite_pool)
        session = result.sessions[0]

        # Timestamps should be ISO strings
        assert isinstance(session.first_timestamp, str)
        assert isinstance(session.last_timestamp, str)
        assert session.first_timestamp == "2025-01-15T10:00:00"
        assert session.last_timestamp == "2025-01-15T10:05:01"

    @pytest.mark.asyncio
    async def test_models_used(self, populated_sqlite_pool: DatabasePool):
        """Test that models are correctly extracted from session."""
        result = await fetch_session_list(limit=10, db_pool=populated_sqlite_pool)
        session = result.sessions[0]

        assert len(session.models_used) == 2
        assert "gpt-4" in session.models_used
        assert "claude-3-sonnet" in session.models_used

    @pytest.mark.asyncio
    async def test_preview_message(self, populated_sqlite_pool: DatabasePool):
        """Test that preview message is extracted from first request."""
        result = await fetch_session_list(limit=10, db_pool=populated_sqlite_pool)
        session = result.sessions[0]

        # Preview should be first user message (skipping probe requests with max_tokens=1)
        assert session.preview_message == "What is 2 + 2?"

    @pytest.mark.asyncio
    async def test_policy_interventions_counted(self, populated_with_interventions_pool: DatabasePool):
        """Test that policy interventions are counted (excluding evaluation events)."""
        result = await fetch_session_list(limit=10, db_pool=populated_with_interventions_pool)

        # Should have session-2 with 1 intervention (evaluation_started/complete excluded)
        sessions = result.sessions
        assert len(sessions) >= 1

        # Find session-2
        session2 = next((s for s in sessions if s.session_id == "session-2"), None)
        assert session2 is not None
        assert session2.policy_interventions == 1  # Only the blocked event counts

    @pytest.mark.asyncio
    async def test_pagination_offset(self, sqlite_pool: DatabasePool):
        """Test pagination with offset."""
        async with sqlite_pool.connection() as conn:
            # Create 3 sessions
            for i in range(3):
                await conn.execute(
                    """
                    INSERT INTO conversation_calls
                    (call_id, model_name, provider, status, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"call-{i}",
                    "gpt-4",
                    "openai",
                    "completed",
                    f"session-{i}",
                    f"2025-01-15T{10 + i:02d}:00:00",
                )

                await conn.execute(
                    """
                    INSERT INTO conversation_events
                    (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"event-{i}",
                    f"call-{i}",
                    "transaction.request_recorded",
                    json.dumps(
                        {
                            "final_model": "gpt-4",
                            "final_request": {"messages": [{"role": "user", "content": f"Message {i}"}]},
                        }
                    ),
                    f"session-{i}",
                    f"2025-01-15T{10 + i:02d}:00:00",
                )

        # Fetch first 2
        result = await fetch_session_list(limit=2, db_pool=sqlite_pool)
        assert len(result.sessions) == 2
        assert result.total == 3
        assert result.has_more is True

        # Fetch next page with offset
        result = await fetch_session_list(limit=2, db_pool=sqlite_pool, offset=2)
        assert len(result.sessions) == 1
        assert result.offset == 2
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_empty_database(self, sqlite_pool: DatabasePool):
        """Test with empty database."""
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool)

        assert result.total == 0
        assert result.sessions == []
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_sessions_ordered_by_last_activity(self, sqlite_pool: DatabasePool):
        """Test that sessions are ordered by most recent activity descending."""
        async with sqlite_pool.connection() as conn:
            # Create sessions with different last timestamps
            for i in range(3):
                await conn.execute(
                    """
                    INSERT INTO conversation_calls
                    (call_id, model_name, provider, status, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"call-{i}",
                    "gpt-4",
                    "openai",
                    "completed",
                    f"session-{i}",
                    f"2025-01-15T10:00:0{i}",
                )

                # Events at different times
                await conn.execute(
                    """
                    INSERT INTO conversation_events
                    (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"event-{i}",
                    f"call-{i}",
                    "transaction.request_recorded",
                    json.dumps(
                        {
                            "final_model": "gpt-4",
                            "final_request": {"messages": [{"role": "user", "content": f"Msg {i}"}]},
                        }
                    ),
                    f"session-{i}",
                    f"2025-01-15T10:00:0{i}",
                )

        result = await fetch_session_list(limit=10, db_pool=sqlite_pool)
        sessions = result.sessions

        # Should be in descending order (most recent first)
        for i in range(len(sessions) - 1):
            assert sessions[i].last_timestamp >= sessions[i + 1].last_timestamp

    @pytest.mark.asyncio
    async def test_session_with_no_preview_message(self, sqlite_pool: DatabasePool):
        """Test session where no preview message can be extracted."""
        async with sqlite_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_calls
                (call_id, model_name, provider, status, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "call-999",
                "gpt-4",
                "openai",
                "completed",
                "session-999",
                "2025-01-15T15:00:00",
            )

            # Event with no messages (will have no preview)
            await conn.execute(
                """
                INSERT INTO conversation_events
                (id, call_id, event_type, payload, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "event-999",
                "call-999",
                "transaction.request_recorded",
                json.dumps(
                    {
                        "final_model": "gpt-4",
                        "final_request": {"messages": []},
                    }
                ),
                "session-999",
                "2025-01-15T15:00:00",
            )

        result = await fetch_session_list(limit=10, db_pool=sqlite_pool)
        assert len(result.sessions) == 1
        assert result.sessions[0].preview_message is None

    @pytest.mark.asyncio
    async def test_multiple_models_in_single_session(self, sqlite_pool: DatabasePool):
        """Test session using multiple distinct models."""
        async with sqlite_pool.connection() as conn:
            models = ["gpt-4", "gpt-3.5-turbo", "claude-3-opus"]

            for idx, model in enumerate(models):
                await conn.execute(
                    """
                    INSERT INTO conversation_calls
                    (call_id, model_name, provider, status, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"call-multi-{idx}",
                    model,
                    "openai" if model.startswith("gpt") else "anthropic",
                    "completed",
                    "session-multi",
                    f"2025-01-15T10:{idx:02d}:00",
                )

                await conn.execute(
                    """
                    INSERT INTO conversation_events
                    (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"event-multi-{idx}",
                    f"call-multi-{idx}",
                    "transaction.request_recorded",
                    json.dumps(
                        {
                            "final_model": model,
                            "final_request": {"messages": [{"role": "user", "content": f"Using {model}"}]},
                        }
                    ),
                    "session-multi",
                    f"2025-01-15T10:{idx:02d}:00",
                )

        result = await fetch_session_list(limit=10, db_pool=sqlite_pool)
        assert len(result.sessions) == 1

        session = result.sessions[0]
        assert len(session.models_used) == 3
        for model in models:
            assert model in session.models_used

    @pytest.mark.asyncio
    async def test_probe_request_skipped_in_preview(self, sqlite_pool: DatabasePool):
        """Test that probe requests (max_tokens=1) are skipped for preview."""
        async with sqlite_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_calls
                (call_id, model_name, provider, status, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "call-probe",
                "gpt-4",
                "openai",
                "completed",
                "session-probe",
                "2025-01-15T16:00:00",
            )

            # First: probe request (max_tokens=1, should be skipped)
            await conn.execute(
                """
                INSERT INTO conversation_events
                (id, call_id, event_type, payload, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "event-probe-1",
                "call-probe",
                "transaction.request_recorded",
                json.dumps(
                    {
                        "final_model": "gpt-4",
                        "final_request": {"max_tokens": 1, "messages": [{"role": "user", "content": "token count"}]},
                    }
                ),
                "session-probe",
                "2025-01-15T16:00:00",
            )

            # Second: real request (should be used for preview)
            await conn.execute(
                """
                INSERT INTO conversation_events
                (id, call_id, event_type, payload, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "event-probe-2",
                "call-probe",
                "transaction.request_recorded",
                json.dumps(
                    {
                        "final_model": "gpt-4",
                        "final_request": {
                            "max_tokens": 1024,
                            "messages": [{"role": "user", "content": "Real question"}],
                        },
                    }
                ),
                "session-probe",
                "2025-01-15T16:00:01",
            )

        result = await fetch_session_list(limit=10, db_pool=sqlite_pool)
        session = result.sessions[0]

        # Should use the real request, not the probe
        assert session.preview_message == "Real question"

    @pytest.mark.asyncio
    async def test_all_policy_intervention_types(self, sqlite_pool: DatabasePool):
        """Test counting various policy intervention event types."""
        async with sqlite_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_calls
                (call_id, model_name, provider, status, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "call-policy",
                "gpt-4",
                "openai",
                "completed",
                "session-policy",
                "2025-01-17T10:00:00",
            )

            await conn.execute(
                """
                INSERT INTO conversation_events
                (id, call_id, event_type, payload, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "event-policy-req",
                "call-policy",
                "transaction.request_recorded",
                json.dumps(
                    {
                        "final_model": "gpt-4",
                        "final_request": {"messages": [{"role": "user", "content": "Test"}]},
                    }
                ),
                "session-policy",
                "2025-01-17T10:00:00",
            )

            # Add multiple policy events (not evaluation)
            policy_events = [
                "policy.judge.tool_call_blocked",
                "policy.all_caps.content_transformed",
                "policy.simple_policy.content_complete_warning",
            ]

            for idx, event_type in enumerate(policy_events):
                await conn.execute(
                    """
                    INSERT INTO conversation_events
                    (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"event-policy-{idx}",
                    "call-policy",
                    event_type,
                    json.dumps({"summary": "Policy event"}),
                    "session-policy",
                    f"2025-01-17T10:00:0{idx + 1}",
                )

            # Add evaluation events (should NOT be counted)
            await conn.execute(
                """
                INSERT INTO conversation_events
                (id, call_id, event_type, payload, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "event-policy-eval",
                "call-policy",
                "policy.judge.evaluation_started",
                json.dumps({}),
                "session-policy",
                "2025-01-17T10:00:04",
            )

        result = await fetch_session_list(limit=10, db_pool=sqlite_pool)
        session = result.sessions[0]

        # Should count 3 policy events, not the evaluation event
        assert session.policy_interventions == 3

    @pytest.mark.asyncio
    async def test_limit_enforced(self, sqlite_pool: DatabasePool):
        """Test that limit parameter is respected."""
        async with sqlite_pool.connection() as conn:
            # Create 5 sessions
            for i in range(5):
                await conn.execute(
                    """
                    INSERT INTO conversation_calls
                    (call_id, model_name, provider, status, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"call-limit-{i}",
                    "gpt-4",
                    "openai",
                    "completed",
                    f"session-limit-{i}",
                    f"2025-01-15T10:{i:02d}:00",
                )

                await conn.execute(
                    """
                    INSERT INTO conversation_events
                    (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"event-limit-{i}",
                    f"call-limit-{i}",
                    "transaction.request_recorded",
                    json.dumps(
                        {
                            "final_model": "gpt-4",
                            "final_request": {"messages": [{"role": "user", "content": f"Msg {i}"}]},
                        }
                    ),
                    f"session-limit-{i}",
                    f"2025-01-15T10:{i:02d}:00",
                )

        result = await fetch_session_list(limit=3, db_pool=sqlite_pool)

        assert len(result.sessions) == 3
        assert result.total == 5
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_is_sqlite_flag_used(self, sqlite_pool: DatabasePool):
        """Test that is_sqlite flag correctly routes to SQLite path."""
        # The pool's is_sqlite property should be True
        assert sqlite_pool.is_sqlite is True

        async with sqlite_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_calls
                (call_id, model_name, provider, status, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "call-flag",
                "gpt-4",
                "openai",
                "completed",
                "session-flag",
                "2025-01-15T12:00:00",
            )

            await conn.execute(
                """
                INSERT INTO conversation_events
                (id, call_id, event_type, payload, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "event-flag",
                "call-flag",
                "transaction.request_recorded",
                json.dumps(
                    {
                        "final_model": "gpt-4",
                        "final_request": {"messages": [{"role": "user", "content": "Test"}]},
                    }
                ),
                "session-flag",
                "2025-01-15T12:00:00",
            )

        # Call fetch_session_list (which will dispatch to SQLite path)
        result = await fetch_session_list(limit=10, db_pool=sqlite_pool)

        assert len(result.sessions) == 1
        assert result.sessions[0].session_id == "session-flag"


__all__ = []
