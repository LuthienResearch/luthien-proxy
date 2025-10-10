"""Test suite for conversation event storage with timezone handling."""

from datetime import UTC, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.control_plane.conversation.models import ConversationEvent
from luthien_proxy.control_plane.conversation.store import record_conversation_events


@pytest.mark.asyncio
async def test_record_events_with_timezone_aware_datetime():
    """Test that timezone-aware datetimes are properly handled when inserting to database.

    Regression test for: "can't subtract offset-naive and offset-aware datetimes" error
    when inserting events with timezone-aware timestamps into postgres timestamp columns.
    """
    # Create a timezone-aware timestamp (this is what we get from the events)
    timestamp_aware = datetime(2025, 1, 15, 10, 30, 45, tzinfo=UTC)

    # Create test events with timezone-aware timestamps
    events = [
        ConversationEvent(
            call_id="test-call-123",
            trace_id=None,
            event_type="request",
            sequence=1000000000,
            timestamp=timestamp_aware,
            hook="async_pre_call_hook",
            payload={
                "messages": [{"role": "user", "content": "Test message"}],
                "model": "gpt-4o",
            },
        ),
        ConversationEvent(
            call_id="test-call-123",
            trace_id=None,
            event_type="response",
            sequence=2000000000,
            timestamp=timestamp_aware,
            hook="async_post_call_success_hook",
            payload={
                "message": {"role": "assistant", "content": "Test response"},
                "finish_reason": "stop",
                "status": "success",
            },
        ),
    ]

    # Mock the database connection and pool
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__.return_value = mock_conn
    mock_pool.connection.return_value.__aexit__.return_value = AsyncMock()

    # Record the events
    await record_conversation_events(mock_pool, events)

    # Verify that execute was called (events were processed)
    assert mock_conn.execute.called

    # Verify that the timestamps passed to postgres are timezone-naive
    # (to avoid "can't subtract offset-naive and offset-aware datetimes" error)
    for call in mock_conn.execute.call_args_list:
        args = call[0]
        # Check all datetime arguments in the call
        for arg in args:
            if isinstance(arg, datetime):
                # All datetime objects passed to postgres should be naive
                assert arg.tzinfo is None, f"Datetime argument should be timezone-naive for postgres, got {arg}"


@pytest.mark.asyncio
async def test_record_events_with_timezone_naive_datetime():
    """Test that timezone-naive datetimes also work correctly."""
    # Create a timezone-naive timestamp
    timestamp_naive = datetime(2025, 1, 15, 10, 30, 45)

    events = [
        ConversationEvent(
            call_id="test-call-456",
            trace_id=None,
            event_type="request",
            sequence=1000000000,
            timestamp=timestamp_naive,
            hook="async_pre_call_hook",
            payload={"messages": [], "model": "gpt-4o"},
        ),
    ]

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__.return_value = mock_conn
    mock_pool.connection.return_value.__aexit__.return_value = AsyncMock()

    # Should not raise any errors
    await record_conversation_events(mock_pool, events)

    assert mock_conn.execute.called


@pytest.mark.asyncio
async def test_record_events_with_mixed_timezone_datetimes():
    """Test handling of both timezone-aware and naive datetimes in the same batch."""
    timestamp_aware = datetime(2025, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
    timestamp_naive = datetime(2025, 1, 15, 10, 31, 0)

    events = [
        ConversationEvent(
            call_id="test-call-789",
            trace_id=None,
            event_type="request",
            sequence=1000000000,
            timestamp=timestamp_aware,
            hook="async_pre_call_hook",
            payload={"messages": [], "model": "gpt-4o"},
        ),
        ConversationEvent(
            call_id="test-call-789",
            trace_id=None,
            event_type="response",
            sequence=2000000000,
            timestamp=timestamp_naive,
            hook="async_post_call_success_hook",
            payload={
                "message": {"content": "Response"},
                "status": "success",
            },
        ),
    ]

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__.return_value = mock_conn
    mock_pool.connection.return_value.__aexit__.return_value = AsyncMock()

    # Should handle both types without errors
    await record_conversation_events(mock_pool, events)

    assert mock_conn.execute.called

    # All datetime args should be naive
    for call in mock_conn.execute.call_args_list:
        args = call[0]
        for arg in args:
            if isinstance(arg, datetime):
                assert arg.tzinfo is None
