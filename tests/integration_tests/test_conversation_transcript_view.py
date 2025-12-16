# ABOUTME: Integration tests for conversation_transcript SQL view
# ABOUTME: Verifies the view correctly extracts prompts/responses from conversation_events

"""Tests for the conversation_transcript database view.

These tests verify the SQL view correctly extracts human-readable content
from the conversation_events table for both prompts and responses.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from luthien_proxy.utils.db import DatabasePool

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def db_pool():
    """Create database connection pool. Skips if DATABASE_URL not set."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    pool = DatabasePool(url=db_url)
    yield pool
    await pool.close()


@pytest.fixture
async def test_call_id(db_pool):
    """Create a test call row and clean up after."""
    call_id = f"test-{uuid.uuid4()}"
    async with db_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO conversation_calls (call_id, model_name, status, created_at) "
            "VALUES ($1, 'test-model', 'completed', NOW())",
            call_id,
        )
    yield call_id
    async with db_pool.connection() as conn:
        await conn.execute("DELETE FROM conversation_calls WHERE call_id = $1", call_id)


# =============================================================================
# Helper to insert test events
# =============================================================================


async def insert_event(conn, call_id: str, event_type: str, payload: dict):
    """Insert a conversation event with the given payload."""
    await conn.execute(
        "INSERT INTO conversation_events (call_id, event_type, payload, created_at) VALUES ($1, $2, $3, NOW())",
        call_id,
        event_type,
        json.dumps(payload),
    )


# =============================================================================
# Tests
# =============================================================================


class TestConversationTranscriptView:
    """Tests for the conversation_transcript SQL view."""

    @pytest.mark.asyncio
    async def test_view_has_expected_columns(self, db_pool):
        """Verify the view returns all expected columns in correct order."""
        async with db_pool.connection() as conn:
            result = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'conversation_transcript' ORDER BY ordinal_position"
            )
        columns = [r["column_name"] for r in result]
        assert columns == [
            "session_id",
            "created_at",
            "prompt_or_response",
            "model",
            "content",
            "logged_by_luthien",
            "call_id",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content_format,payload,expected_content",
        [
            # OpenAI format: content is a string
            (
                "string",
                {"payload": {"model": "test", "messages": [{"role": "user", "content": "Hello!"}]}},
                "Hello!",
            ),
            # Anthropic format: content is array of blocks
            (
                "array",
                {
                    "payload": {
                        "model": "test",
                        "messages": [
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": "Part1"}, {"type": "text", "text": "Part2"}],
                            }
                        ],
                    }
                },
                "Part1 Part2",
            ),
        ],
        ids=["string_content", "array_content"],
    )
    async def test_prompt_content_extraction(self, db_pool, test_call_id, content_format, payload, expected_content):
        """Test that prompts are extracted correctly for both OpenAI and Anthropic formats."""
        async with db_pool.connection() as conn:
            await insert_event(conn, test_call_id, "pipeline.client_request", payload)
            rows = await conn.fetch(
                "SELECT prompt_or_response, content FROM conversation_transcript WHERE call_id = $1",
                test_call_id,
            )
        assert len(rows) == 1
        assert rows[0]["prompt_or_response"] == "PROMPT"
        assert rows[0]["content"] == expected_content

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "event_type",
        ["transaction.streaming_response_recorded", "transaction.non_streaming_response_recorded"],
        ids=["streaming", "non_streaming"],
    )
    async def test_response_content_extraction(self, db_pool, test_call_id, event_type):
        """Test that responses are extracted correctly for both streaming and non-streaming."""
        payload = {"final_response": {"model": "test", "choices": [{"message": {"content": "Hello back!"}}]}}
        async with db_pool.connection() as conn:
            await insert_event(conn, test_call_id, event_type, payload)
            rows = await conn.fetch(
                "SELECT prompt_or_response, content FROM conversation_transcript WHERE call_id = $1",
                test_call_id,
            )
        assert len(rows) == 1
        assert rows[0]["prompt_or_response"] == "RESPONSE"
        assert rows[0]["content"] == "Hello back!"

    @pytest.mark.asyncio
    async def test_logged_by_luthien_always_y(self, db_pool, test_call_id):
        """Verify logged_by_luthien column is always 'Y' for all records."""
        payload = {"payload": {"model": "test", "messages": [{"role": "user", "content": "test"}]}}
        async with db_pool.connection() as conn:
            await insert_event(conn, test_call_id, "pipeline.client_request", payload)
            rows = await conn.fetch(
                "SELECT logged_by_luthien FROM conversation_transcript WHERE call_id = $1",
                test_call_id,
            )
        assert rows[0]["logged_by_luthien"] == "Y"

    @pytest.mark.asyncio
    async def test_filters_intermediate_events(self, db_pool, test_call_id):
        """Verify view excludes intermediate events (format_conversion, backend_request, etc.)."""
        # Insert mix of event types - only 2 should appear in view
        events = [
            ("pipeline.client_request", {"payload": {"model": "t", "messages": [{"role": "user", "content": "hi"}]}}),
            ("pipeline.format_conversion", {"from": "anthropic", "to": "openai"}),
            ("transaction.request_recorded", {"original_request": {}}),
            ("pipeline.backend_request", {"payload": {}}),
            (
                "transaction.streaming_response_recorded",
                {"final_response": {"model": "t", "choices": [{"message": {"content": "bye"}}]}},
            ),
        ]
        async with db_pool.connection() as conn:
            for event_type, payload in events:
                await insert_event(conn, test_call_id, event_type, payload)
            rows = await conn.fetch(
                "SELECT prompt_or_response FROM conversation_transcript WHERE call_id = $1 ORDER BY created_at",
                test_call_id,
            )
        # Only PROMPT and RESPONSE should appear (not the 3 intermediate events)
        assert len(rows) == 2
        assert [r["prompt_or_response"] for r in rows] == ["PROMPT", "RESPONSE"]
