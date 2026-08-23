from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from luthien_proxy.debug.service import fetch_call_diff, fetch_call_events
from luthien_proxy.history.service import fetch_session_detail
from luthien_proxy.passthrough_materialize.materialize import materialize_transaction
from luthien_proxy.passthrough_materialize.materialize_types import (
    AlreadyMaterialized,
    MaterializationFailed,
    Materialized,
    SkippedIneligible,
)
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations


@pytest.fixture
async def materialize_pool() -> AsyncIterator[DatabasePool]:
    pool = DatabasePool("sqlite://:memory:")
    await check_migrations(pool)
    yield pool
    await pool.close()


async def _seed_openai_chat_transaction(pool: DatabasePool, transaction_id: str) -> None:
    request_body = {
        "model": "gpt-4.1",
        "messages": [{"role": "user", "content": "Hello from passthrough."}],
    }
    response_body = {
        "id": "chatcmpl-materialized",
        "model": "gpt-4.1",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Hello back."}}],
    }
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO request_logs (
                id, transaction_id, session_id, user_id, direction, request_body,
                response_status, response_body, started_at, completed_at, model,
                is_streaming, endpoint, error
            ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9, $10, $11, $12, $13, $14)
            """,
            "log-inbound",
            transaction_id,
            "session-materialized",
            "user-materialized",
            "inbound",
            json.dumps(request_body),
            200,
            json.dumps(response_body),
            "2026-07-11T10:00:00+00:00",
            "2026-07-11T10:00:01+00:00",
            "gpt-4.1",
            False,
            "/openai/v1/chat/completions",
            None,
        )
        await conn.execute(
            """
            INSERT INTO request_logs (
                id, transaction_id, session_id, user_id, direction, request_body,
                response_status, response_body, started_at, completed_at, model,
                is_streaming, endpoint, error
            ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9, $10, $11, $12, $13, $14)
            """,
            "log-outbound",
            transaction_id,
            "session-materialized",
            "user-materialized",
            "outbound",
            json.dumps({"model": "must-not-win", "messages": []}),
            200,
            json.dumps(response_body),
            "2026-07-11T10:00:00+00:00",
            "2026-07-11T10:00:01+00:00",
            "must-not-win",
            False,
            "/openai/v1/chat/completions",
            None,
        )


async def test_materializes_openai_chat_transaction_when_paired_raw_logs_exist(materialize_pool: DatabasePool) -> None:
    # Given
    transaction_id = "transaction-materialized"
    await _seed_openai_chat_transaction(materialize_pool, transaction_id)

    # When
    result = await materialize_transaction(materialize_pool, transaction_id)

    # Then
    assert isinstance(result, Materialized)
    async with materialize_pool.connection() as conn:
        event_rows = await conn.fetch(
            "SELECT event_type, payload, created_at FROM conversation_events WHERE call_id = $1 ORDER BY created_at",
            transaction_id,
        )
        call_row = await conn.fetchrow("SELECT * FROM conversation_calls WHERE call_id = $1", transaction_id)
        summary_row = await conn.fetchrow(
            "SELECT * FROM session_summaries WHERE session_id = $1", "session-materialized"
        )

    assert [row["event_type"] for row in event_rows] == [
        "transaction.request_recorded",
        "transaction.non_streaming_response_recorded",
    ]
    assert str(event_rows[0]["created_at"]) < str(event_rows[1]["created_at"])
    assert call_row is not None
    assert call_row["session_id"] == "session-materialized"
    assert call_row["user_id"] == "user-materialized"
    assert summary_row is not None
    assert summary_row["event_count"] == 2
    assert summary_row["call_count"] == 1
    assert summary_row["models_used"] == "gpt-4.1"
    assert str(summary_row["first_seen"]) == str(event_rows[0]["created_at"])
    assert str(summary_row["last_seen"]) == str(event_rows[1]["created_at"])
    request_payload = json.loads(str(event_rows[0]["payload"]))
    response_payload = json.loads(str(event_rows[1]["payload"]))
    assert request_payload["provider_request"]["model"] == "gpt-4.1"
    assert response_payload["final_response"]["content"] == [{"type": "text", "text": "Hello back."}]

    history = await fetch_session_detail("session-materialized", materialize_pool)
    debug_events = await fetch_call_events(transaction_id, materialize_pool)
    debug_diff = await fetch_call_diff(transaction_id, materialize_pool)
    async with materialize_pool.connection() as conn:
        fts_rows = await conn.fetch(
            "SELECT content FROM conversation_events_fts WHERE session_id = $1", "session-materialized"
        )

    assert history.turns[0].request_messages[0].content == "Hello from passthrough."
    assert history.turns[0].response_messages[0].content == "Hello back."
    assert debug_events.events[0].payload["provider"] == "openai"
    assert debug_diff.request is not None
    assert debug_diff.request.model_changed is False
    assert fts_rows[0]["content"] == "Hello from passthrough."


async def test_returns_already_materialized_when_transaction_is_replayed(materialize_pool: DatabasePool) -> None:
    # Given
    transaction_id = "transaction-idempotent"
    await _seed_openai_chat_transaction(materialize_pool, transaction_id)
    await materialize_transaction(materialize_pool, transaction_id)

    # When
    result = await materialize_transaction(materialize_pool, transaction_id)

    # Then
    assert isinstance(result, AlreadyMaterialized)
    async with materialize_pool.connection() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM conversation_events WHERE call_id = $1", transaction_id)
    assert count == 2


async def test_materializes_gemini_stream_when_reassembled_capture_is_recorded(materialize_pool: DatabasePool) -> None:
    # Given
    transaction_id = "transaction-gemini-stream"
    await _seed_openai_chat_transaction(materialize_pool, transaction_id)
    request_body = {"contents": [{"role": "user", "parts": [{"text": "Say hello."}]}]}
    response_body = {
        "stream_format": "gemini-json-array",
        "chunks": [
            {"candidates": [{"index": 0, "content": {"role": "model", "parts": [{"text": "Hel"}]}}]},
            {
                "candidates": [
                    {"index": 0, "content": {"role": "model", "parts": [{"text": "lo"}]}, "finishReason": "STOP"}
                ]
            },
        ],
        "final": None,
    }
    async with materialize_pool.connection() as conn:
        await conn.execute(
            """
            UPDATE request_logs
            SET request_body = $1::jsonb, response_body = $2::jsonb, endpoint = $3, is_streaming = $4, model = $5
            WHERE transaction_id = $6
            """,
            json.dumps(request_body),
            json.dumps(response_body),
            "/gemini/v1beta/models/gemini-2.5-pro:streamGenerateContent",
            True,
            "gemini-2.5-pro",
            transaction_id,
        )

    # When
    result = await materialize_transaction(materialize_pool, transaction_id)

    # Then
    assert isinstance(result, Materialized)
    async with materialize_pool.connection() as conn:
        response_row = await conn.fetchrow(
            "SELECT payload FROM conversation_events WHERE call_id = $1 AND event_type = $2",
            transaction_id,
            "transaction.streaming_response_recorded",
        )
    assert response_row is not None
    response_payload = json.loads(str(response_row["payload"]))
    assert response_payload["provider"] == "gemini"
    assert response_payload["provider_response"] == response_body
    assert response_payload["final_response"]["content"] == [{"type": "text", "text": "Hello"}]


async def test_preserves_absent_user_id_when_request_log_has_no_identity(materialize_pool: DatabasePool) -> None:
    # Given
    transaction_id = "transaction-no-user"
    await _seed_openai_chat_transaction(materialize_pool, transaction_id)
    async with materialize_pool.connection() as conn:
        await conn.execute("UPDATE request_logs SET user_id = NULL WHERE transaction_id = $1", transaction_id)

    # When
    await materialize_transaction(materialize_pool, transaction_id)

    # Then
    async with materialize_pool.connection() as conn:
        call_row = await conn.fetchrow("SELECT user_id FROM conversation_calls WHERE call_id = $1", transaction_id)
        summary_row = await conn.fetchrow(
            "SELECT user_id FROM session_summaries WHERE session_id = $1", "session-materialized"
        )
    assert call_row is not None
    assert summary_row is not None
    assert call_row["user_id"] is None
    assert summary_row["user_id"] is None


async def test_materializes_upstream_error_when_capture_has_error_without_response_body(
    materialize_pool: DatabasePool,
) -> None:
    # Given
    transaction_id = "transaction-upstream-error"
    await _seed_openai_chat_transaction(materialize_pool, transaction_id)
    async with materialize_pool.connection() as conn:
        await conn.execute(
            "UPDATE request_logs SET response_body = NULL, response_status = $1, error = $2 WHERE transaction_id = $3",
            502,
            "ConnectError: upstream unavailable",
            transaction_id,
        )

    # When
    result = await materialize_transaction(materialize_pool, transaction_id)

    # Then
    assert isinstance(result, Materialized)
    async with materialize_pool.connection() as conn:
        call_row = await conn.fetchrow("SELECT status FROM conversation_calls WHERE call_id = $1", transaction_id)
        response_row = await conn.fetchrow(
            "SELECT payload FROM conversation_events WHERE call_id = $1 AND event_type = $2",
            transaction_id,
            "transaction.non_streaming_response_recorded",
        )
    assert call_row is not None
    assert response_row is not None
    assert call_row["status"] == "error"
    assert json.loads(str(response_row["payload"]))["final_response"] == {
        "role": "assistant",
        "content": [],
        "stop_reason": "error",
        "error": {"status_code": 502, "body": {"error": "ConnectError: upstream unavailable"}},
    }


async def test_skips_ineligible_endpoint_before_parsing_malformed_capture(materialize_pool: DatabasePool) -> None:
    # Given
    transaction_id = "transaction-ineligible"
    await _seed_openai_chat_transaction(materialize_pool, transaction_id)
    async with materialize_pool.connection() as conn:
        await conn.execute(
            "UPDATE request_logs SET endpoint = $1, request_body = $2, response_body = $2 WHERE transaction_id = $3",
            "/openai/v1/models",
            "not-json",
            transaction_id,
        )

    # When
    result = await materialize_transaction(materialize_pool, transaction_id)

    # Then
    assert isinstance(result, SkippedIneligible)
    async with materialize_pool.connection() as conn:
        event_count = await conn.fetchval("SELECT COUNT(*) FROM conversation_events WHERE call_id = $1", transaction_id)
        call_count = await conn.fetchval("SELECT COUNT(*) FROM conversation_calls WHERE call_id = $1", transaction_id)
        summary_count = await conn.fetchval(
            "SELECT COUNT(*) FROM session_summaries WHERE session_id = $1", "session-materialized"
        )
    assert event_count == 0
    assert call_count == 0
    assert summary_count == 0


async def test_keeps_normalization_failure_retryable_without_request_event(materialize_pool: DatabasePool) -> None:
    # Given
    transaction_id = "transaction-normalize-failure"
    await _seed_openai_chat_transaction(materialize_pool, transaction_id)
    async with materialize_pool.connection() as conn:
        await conn.execute(
            "UPDATE request_logs SET request_body = $1::jsonb WHERE transaction_id = $2",
            json.dumps({"model": "gpt-4.1"}),
            transaction_id,
        )

    # When
    first_result = await materialize_transaction(materialize_pool, transaction_id)
    retry_result = await materialize_transaction(materialize_pool, transaction_id)

    # Then
    assert isinstance(first_result, MaterializationFailed)
    assert first_result.reason == "missing_required_field"
    assert isinstance(retry_result, MaterializationFailed)
    async with materialize_pool.connection() as conn:
        event_count = await conn.fetchval("SELECT COUNT(*) FROM conversation_events WHERE call_id = $1", transaction_id)
        call_count = await conn.fetchval("SELECT COUNT(*) FROM conversation_calls WHERE call_id = $1", transaction_id)
        summary_count = await conn.fetchval(
            "SELECT COUNT(*) FROM session_summaries WHERE session_id = $1", "session-materialized"
        )
    assert event_count == 0
    assert call_count == 0
    assert summary_count == 0
