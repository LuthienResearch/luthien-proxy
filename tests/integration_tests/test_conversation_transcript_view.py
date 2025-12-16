# ABOUTME: Integration tests for conversation_transcript SQL view
# ABOUTME: Tests that the view correctly extracts prompts/responses from conversation_events

"""Integration tests for the conversation_transcript database view.

These tests verify that the SQL view correctly:
- Extracts content from pipeline.client_request events (prompts)
- Extracts content from transaction.*_response_recorded events (responses)
- Handles both string and array (Anthropic) content formats
- Returns expected columns with correct types
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from luthien_proxy.utils.db import DatabasePool


@pytest.fixture
async def db_pool():
    """Create a database connection pool for tests."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set - skipping integration test")
    pool = DatabasePool(url=db_url)
    yield pool
    await pool.close()


@pytest.fixture
async def test_call_id(db_pool):
    """Create a test call and return its ID. Cleans up after test."""
    call_id = f"test-{uuid.uuid4()}"

    async with db_pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO conversation_calls (call_id, model_name, status, created_at)
            VALUES ($1, 'test-model', 'completed', NOW())
            """,
            call_id,
        )

    yield call_id

    # Cleanup
    async with db_pool.connection() as conn:
        await conn.execute(
            "DELETE FROM conversation_calls WHERE call_id = $1",
            call_id,
        )


class TestConversationTranscriptView:
    """Tests for the conversation_transcript SQL view."""

    @pytest.mark.asyncio
    async def test_view_returns_expected_columns(self, db_pool):
        """Verify the view returns all expected columns."""
        async with db_pool.connection() as conn:
            # Just get column names by querying with LIMIT 0
            rows = await conn.fetch(
                """
                SELECT * FROM conversation_transcript LIMIT 0
                """
            )
            # Get column names from the result
            if rows:
                columns = list(rows[0].keys())
            else:
                # Query to get column names even with no rows
                result = await conn.fetch(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'conversation_transcript'
                    ORDER BY ordinal_position
                    """
                )
                columns = [r["column_name"] for r in result]

        expected_columns = [
            "session_id",
            "created_at",
            "prompt_or_response",
            "model",
            "content",
            "logged_by_luthien",
            "call_id",
        ]
        assert columns == expected_columns

    @pytest.mark.asyncio
    async def test_extracts_string_content_from_prompt(self, db_pool, test_call_id):
        """Test that string content is extracted correctly from prompts."""
        # Insert a client_request event with string content
        payload = {
            "payload": {
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hello, world!"}],
            }
        }

        async with db_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_events (call_id, event_type, payload, created_at)
                VALUES ($1, 'pipeline.client_request', $2, NOW())
                """,
                test_call_id,
                json.dumps(payload),
            )

            # Query the view
            rows = await conn.fetch(
                """
                SELECT prompt_or_response, model, content
                FROM conversation_transcript
                WHERE call_id = $1
                """,
                test_call_id,
            )

        assert len(rows) == 1
        assert rows[0]["prompt_or_response"] == "PROMPT"
        assert rows[0]["model"] == "test-model"
        assert rows[0]["content"] == "Hello, world!"

    @pytest.mark.asyncio
    async def test_extracts_array_content_from_prompt(self, db_pool, test_call_id):
        """Test that array (Anthropic) content is extracted correctly from prompts."""
        # Insert a client_request event with array content (Anthropic format)
        payload = {
            "payload": {
                "model": "claude-test",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "First part"},
                            {"type": "text", "text": "Second part"},
                        ],
                    }
                ],
            }
        }

        async with db_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_events (call_id, event_type, payload, created_at)
                VALUES ($1, 'pipeline.client_request', $2, NOW())
                """,
                test_call_id,
                json.dumps(payload),
            )

            rows = await conn.fetch(
                """
                SELECT content
                FROM conversation_transcript
                WHERE call_id = $1
                """,
                test_call_id,
            )

        assert len(rows) == 1
        # Array content blocks should be joined with space
        assert rows[0]["content"] == "First part Second part"

    @pytest.mark.asyncio
    async def test_extracts_response_content(self, db_pool, test_call_id):
        """Test that response content is extracted correctly."""
        # Insert a streaming response event
        payload = {
            "final_response": {
                "model": "response-model",
                "choices": [{"message": {"role": "assistant", "content": "Hello back!"}}],
            }
        }

        async with db_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_events (call_id, event_type, payload, created_at)
                VALUES ($1, 'transaction.streaming_response_recorded', $2, NOW())
                """,
                test_call_id,
                json.dumps(payload),
            )

            rows = await conn.fetch(
                """
                SELECT prompt_or_response, model, content
                FROM conversation_transcript
                WHERE call_id = $1
                """,
                test_call_id,
            )

        assert len(rows) == 1
        assert rows[0]["prompt_or_response"] == "RESPONSE"
        assert rows[0]["model"] == "response-model"
        assert rows[0]["content"] == "Hello back!"

    @pytest.mark.asyncio
    async def test_logged_by_luthien_always_y(self, db_pool, test_call_id):
        """Test that logged_by_luthien is always 'Y'."""
        payload = {
            "payload": {
                "model": "test",
                "messages": [{"role": "user", "content": "test"}],
            }
        }

        async with db_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_events (call_id, event_type, payload, created_at)
                VALUES ($1, 'pipeline.client_request', $2, NOW())
                """,
                test_call_id,
                json.dumps(payload),
            )

            rows = await conn.fetch(
                """
                SELECT logged_by_luthien
                FROM conversation_transcript
                WHERE call_id = $1
                """,
                test_call_id,
            )

        assert len(rows) == 1
        assert rows[0]["logged_by_luthien"] == "Y"

    @pytest.mark.asyncio
    async def test_handles_null_session_id(self, db_pool, test_call_id):
        """Test that NULL session_id is handled correctly."""
        payload = {
            "payload": {
                "model": "test",
                "messages": [{"role": "user", "content": "test"}],
            }
        }

        async with db_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_events (call_id, event_type, payload, created_at, session_id)
                VALUES ($1, 'pipeline.client_request', $2, NOW(), NULL)
                """,
                test_call_id,
                json.dumps(payload),
            )

            rows = await conn.fetch(
                """
                SELECT session_id
                FROM conversation_transcript
                WHERE call_id = $1
                """,
                test_call_id,
            )

        assert len(rows) == 1
        assert rows[0]["session_id"] is None

    @pytest.mark.asyncio
    async def test_filters_irrelevant_event_types(self, db_pool, test_call_id):
        """Test that non-prompt/response events are filtered out."""
        # Insert events of various types
        events = [
            (
                "pipeline.client_request",
                {"payload": {"model": "test", "messages": [{"role": "user", "content": "prompt"}]}},
            ),
            ("pipeline.format_conversion", {"from_format": "anthropic", "to_format": "openai"}),
            ("transaction.request_recorded", {"original_request": {}, "final_request": {}}),
            ("pipeline.backend_request", {"payload": {}}),
            (
                "transaction.streaming_response_recorded",
                {"final_response": {"model": "test", "choices": [{"message": {"content": "response"}}]}},
            ),
        ]

        async with db_pool.connection() as conn:
            for event_type, payload in events:
                await conn.execute(
                    """
                    INSERT INTO conversation_events (call_id, event_type, payload, created_at)
                    VALUES ($1, $2, $3, NOW())
                    """,
                    test_call_id,
                    event_type,
                    json.dumps(payload),
                )

            rows = await conn.fetch(
                """
                SELECT prompt_or_response
                FROM conversation_transcript
                WHERE call_id = $1
                ORDER BY created_at
                """,
                test_call_id,
            )

        # Should only see PROMPT and RESPONSE, not the intermediate events
        assert len(rows) == 2
        assert rows[0]["prompt_or_response"] == "PROMPT"
        assert rows[1]["prompt_or_response"] == "RESPONSE"
