"""Unit tests for conversation event persistence.

Tests cover:
1. SequentialTaskQueue behavior
2. build_conversation_events() for request and response hooks
3. record_conversation_events() with mocked database
4. publish_conversation_event() with mocked Redis
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.storage.persistence import (
    ConversationEvent,
    SequentialTaskQueue,
    _apply_request_event,
    _apply_response_event,
    _ensure_call_row,
    _insert_event_row,
    build_conversation_events,
    publish_conversation_event,
    record_conversation_events,
)


class TestConversationEventModel:
    """Test ConversationEvent Pydantic model."""

    def test_create_request_event(self):
        """Test creating a request event."""
        event = ConversationEvent(
            call_id="test-call-id",
            event_type="request",
            timestamp=datetime.now(timezone.utc),
            hook="request",
            payload={"messages": [{"role": "user", "content": "Hello"}]},
        )
        assert event.call_id == "test-call-id"
        assert event.event_type == "request"
        assert event.hook == "request"
        assert event.trace_id is None  # Deprecated

    def test_create_response_event(self):
        """Test creating a response event."""
        event = ConversationEvent(
            call_id="test-call-id",
            event_type="response",
            timestamp=datetime.now(timezone.utc),
            hook="response",
            payload={"choices": [], "model": "gpt-4"},
        )
        assert event.call_id == "test-call-id"
        assert event.event_type == "response"


class TestSequentialTaskQueue:
    """Test SequentialTaskQueue behavior."""

    @pytest.mark.asyncio
    async def test_submit_and_execute_task(self):
        """Test that submitted tasks are executed."""
        queue = SequentialTaskQueue("test")
        executed = []

        async def task():
            executed.append(1)

        queue.submit(task())

        # Allow the task to run
        await asyncio.sleep(0.1)

        assert executed == [1]

    @pytest.mark.asyncio
    async def test_tasks_execute_in_order(self):
        """Test that tasks execute in FIFO order."""
        queue = SequentialTaskQueue("test")
        results = []

        async def task(value):
            await asyncio.sleep(0.01)
            results.append(value)

        queue.submit(task(1))
        queue.submit(task(2))
        queue.submit(task(3))

        # Wait for all tasks
        await asyncio.sleep(0.2)

        assert results == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_worker_restarts_after_empty(self):
        """Test that worker restarts when new tasks are added after queue empties."""
        queue = SequentialTaskQueue("test")
        results = []

        async def task(value):
            results.append(value)

        # First batch
        queue.submit(task(1))
        await asyncio.sleep(0.1)
        assert results == [1]

        # Second batch after queue is empty
        queue.submit(task(2))
        await asyncio.sleep(0.1)
        assert results == [1, 2]


class TestBuildConversationEvents:
    """Test build_conversation_events function."""

    def test_returns_empty_for_none_call_id(self):
        """Test that empty list is returned when call_id is None."""
        events = build_conversation_events(
            hook="request",
            call_id=None,
            trace_id=None,
            original={"data": {}},
            result={"data": {}},
            timestamp_ns_fallback=0,
            timestamp=datetime.now(timezone.utc),
        )
        assert events == []

    def test_returns_empty_for_empty_call_id(self):
        """Test that empty list is returned when call_id is empty string."""
        events = build_conversation_events(
            hook="request",
            call_id="",
            trace_id=None,
            original={"data": {}},
            result={"data": {}},
            timestamp_ns_fallback=0,
            timestamp=datetime.now(timezone.utc),
        )
        assert events == []

    def test_build_request_event(self):
        """Test building a request event."""
        timestamp = datetime.now(timezone.utc)
        events = build_conversation_events(
            hook="request",
            call_id="test-call-123",
            trace_id=None,
            original={
                "data": {
                    "messages": [{"role": "user", "content": "Hello"}],
                    "model": "gpt-4",
                    "temperature": 0.7,
                }
            },
            result={
                "data": {
                    "messages": [{"role": "user", "content": "Hello modified"}],
                    "model": "gpt-4",
                    "temperature": 0.7,
                }
            },
            timestamp_ns_fallback=0,
            timestamp=timestamp,
        )

        assert len(events) == 1
        event = events[0]
        assert event.call_id == "test-call-123"
        assert event.event_type == "request"
        assert event.hook == "request"
        assert event.timestamp == timestamp
        # Check payload structure
        assert "original" in event.payload
        assert "final" in event.payload
        assert event.payload["original"]["messages"] == [{"role": "user", "content": "Hello"}]
        assert event.payload["final"]["messages"] == [{"role": "user", "content": "Hello modified"}]

    def test_build_response_event(self):
        """Test building a response event."""
        timestamp = datetime.now(timezone.utc)
        events = build_conversation_events(
            hook="response",
            call_id="test-call-123",
            trace_id=None,
            original={
                "response": {
                    "choices": [{"message": {"content": "Original response"}}],
                    "model": "gpt-4",
                }
            },
            result={
                "response": {
                    "choices": [{"message": {"content": "Modified response"}}],
                    "model": "gpt-4",
                }
            },
            timestamp_ns_fallback=0,
            timestamp=timestamp,
        )

        assert len(events) == 1
        event = events[0]
        assert event.call_id == "test-call-123"
        assert event.event_type == "response"
        assert event.hook == "response"
        # Check payload structure
        assert "original" in event.payload
        assert "final" in event.payload
        assert event.payload["status"] == "success"

    def test_build_request_event_invalid_original(self):
        """Test that invalid original data returns empty list."""
        events = build_conversation_events(
            hook="request",
            call_id="test-call-123",
            trace_id=None,
            original="not a dict",
            result={"data": {}},
            timestamp_ns_fallback=0,
            timestamp=datetime.now(timezone.utc),
        )
        assert events == []

    def test_build_request_event_missing_data(self):
        """Test that missing data key returns empty list."""
        events = build_conversation_events(
            hook="request",
            call_id="test-call-123",
            trace_id=None,
            original={"not_data": {}},
            result={"data": {}},
            timestamp_ns_fallback=0,
            timestamp=datetime.now(timezone.utc),
        )
        assert events == []

    def test_build_response_event_invalid_response(self):
        """Test that invalid response data returns empty list."""
        events = build_conversation_events(
            hook="response",
            call_id="test-call-123",
            trace_id=None,
            original={"response": "not a dict"},
            result={"response": {}},
            timestamp_ns_fallback=0,
            timestamp=datetime.now(timezone.utc),
        )
        assert events == []

    def test_unknown_hook_returns_empty(self):
        """Test that unknown hook returns empty list."""
        events = build_conversation_events(
            hook="unknown_hook",
            call_id="test-call-123",
            trace_id=None,
            original={},
            result={},
            timestamp_ns_fallback=0,
            timestamp=datetime.now(timezone.utc),
        )
        assert events == []

    def test_request_event_filters_none_values(self):
        """Test that None values are filtered from payload."""
        events = build_conversation_events(
            hook="request",
            call_id="test-call-123",
            trace_id=None,
            original={
                "data": {
                    "messages": [{"role": "user", "content": "Hello"}],
                    "model": "gpt-4",
                    # temperature, max_tokens, tools, tool_choice are None
                }
            },
            result={
                "data": {
                    "messages": [{"role": "user", "content": "Hello"}],
                    "model": "gpt-4",
                }
            },
            timestamp_ns_fallback=0,
            timestamp=datetime.now(timezone.utc),
        )

        assert len(events) == 1
        # None values should be filtered out from original/final
        assert "temperature" not in events[0].payload["original"]
        assert "max_tokens" not in events[0].payload["original"]


class TestRecordConversationEvents:
    """Test record_conversation_events function with mocked database."""

    @pytest.mark.asyncio
    async def test_returns_early_if_pool_is_none(self):
        """Test that function returns early when pool is None."""
        events = [
            ConversationEvent(
                call_id="test",
                event_type="request",
                timestamp=datetime.now(timezone.utc),
                hook="request",
                payload={},
            )
        ]
        # Should not raise
        await record_conversation_events(None, events)

    @pytest.mark.asyncio
    async def test_returns_early_if_events_empty(self):
        """Test that function returns early when events is empty."""
        mock_pool = MagicMock()
        # Should not raise or call connection
        await record_conversation_events(mock_pool, [])
        mock_pool.connection.assert_not_called()

    @pytest.mark.asyncio
    async def test_records_request_event(self):
        """Test recording a request event."""
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        event = ConversationEvent(
            call_id="test-call-123",
            event_type="request",
            timestamp=datetime.now(timezone.utc),
            hook="request",
            payload={"model": "gpt-4", "messages": []},
        )

        await record_conversation_events(mock_pool, [event])

        # Should have called execute multiple times (ensure_call_row, apply_request_event, insert_event_row)
        assert mock_conn.execute.call_count >= 3

    @pytest.mark.asyncio
    async def test_records_response_event(self):
        """Test recording a response event."""
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        event = ConversationEvent(
            call_id="test-call-123",
            event_type="response",
            timestamp=datetime.now(timezone.utc),
            hook="response",
            payload={"status": "success"},
        )

        await record_conversation_events(mock_pool, [event])

        # Should have called execute multiple times
        assert mock_conn.execute.call_count >= 3


class TestEnsureCallRow:
    """Test _ensure_call_row helper function."""

    @pytest.mark.asyncio
    async def test_inserts_call_row(self):
        """Test that call row is inserted."""
        mock_conn = AsyncMock()
        event = ConversationEvent(
            call_id="test-call-123",
            event_type="request",
            timestamp=datetime.now(timezone.utc),
            hook="request",
            payload={},
        )

        await _ensure_call_row(mock_conn, event)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "INSERT INTO conversation_calls" in call_args[0][0]
        assert call_args[0][1] == "test-call-123"

    @pytest.mark.asyncio
    async def test_strips_timezone(self):
        """Test that timezone is stripped from timestamp."""
        mock_conn = AsyncMock()
        event = ConversationEvent(
            call_id="test-call-123",
            event_type="request",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            hook="request",
            payload={},
        )

        await _ensure_call_row(mock_conn, event)

        call_args = mock_conn.execute.call_args
        # The timestamp should be naive (no tzinfo)
        assert call_args[0][2].tzinfo is None


class TestApplyRequestEvent:
    """Test _apply_request_event helper function."""

    @pytest.mark.asyncio
    async def test_updates_call_with_model(self):
        """Test that call is updated with model name."""
        mock_conn = AsyncMock()
        event = ConversationEvent(
            call_id="test-call-123",
            event_type="request",
            timestamp=datetime.now(timezone.utc),
            hook="request",
            payload={"model": "gpt-4"},
        )

        await _apply_request_event(mock_conn, event)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "UPDATE conversation_calls" in call_args[0][0]
        assert call_args[0][2] == "gpt-4"

    @pytest.mark.asyncio
    async def test_handles_non_string_model(self):
        """Test that non-string model is handled."""
        mock_conn = AsyncMock()
        event = ConversationEvent(
            call_id="test-call-123",
            event_type="request",
            timestamp=datetime.now(timezone.utc),
            hook="request",
            payload={"model": 123},  # Not a string
        )

        await _apply_request_event(mock_conn, event)

        call_args = mock_conn.execute.call_args
        assert call_args[0][2] is None  # Should be None for non-string


class TestApplyResponseEvent:
    """Test _apply_response_event helper function."""

    @pytest.mark.asyncio
    async def test_updates_call_with_status(self):
        """Test that call is updated with status."""
        mock_conn = AsyncMock()
        event = ConversationEvent(
            call_id="test-call-123",
            event_type="response",
            timestamp=datetime.now(timezone.utc),
            hook="response",
            payload={"status": "success"},
        )

        await _apply_response_event(mock_conn, event)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "UPDATE conversation_calls" in call_args[0][0]
        assert call_args[0][2] == "success"


class TestInsertEventRow:
    """Test _insert_event_row helper function."""

    @pytest.mark.asyncio
    async def test_inserts_event(self):
        """Test that event is inserted."""
        mock_conn = AsyncMock()
        event = ConversationEvent(
            call_id="test-call-123",
            event_type="request",
            timestamp=datetime.now(timezone.utc),
            hook="request",
            payload={"key": "value"},
        )

        await _insert_event_row(mock_conn, event)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "INSERT INTO conversation_events" in call_args[0][0]
        assert call_args[0][1] == "test-call-123"
        assert call_args[0][2] == "request"


class TestPublishConversationEvent:
    """Test publish_conversation_event function."""

    @pytest.mark.asyncio
    async def test_publishes_event_to_redis(self):
        """Test that event is published to Redis."""
        mock_redis = AsyncMock()
        event = ConversationEvent(
            call_id="test-call-123",
            event_type="request",
            timestamp=datetime.now(timezone.utc),
            hook="request",
            payload={"key": "value"},
        )

        await publish_conversation_event(mock_redis, event)

        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == "luthien:conversation:test-call-123"

    @pytest.mark.asyncio
    async def test_returns_early_if_no_call_id(self):
        """Test that function returns early when call_id is empty."""
        mock_redis = AsyncMock()
        event = ConversationEvent(
            call_id="",
            event_type="request",
            timestamp=datetime.now(timezone.utc),
            hook="request",
            payload={},
        )

        await publish_conversation_event(mock_redis, event)

        mock_redis.publish.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
