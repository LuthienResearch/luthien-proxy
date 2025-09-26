"""Test suite for conversation event building and edge cases."""

from datetime import UTC, datetime

from luthien_proxy.control_plane.conversation.events import (
    build_conversation_events,
    clear_stream_indices,
    next_chunk_index,
    reset_stream_indices,
)


def test_reset_stream_indices():
    """Test that stream indices are properly initialized."""
    call_id = "test-call-1"
    reset_stream_indices(call_id)

    # Verify both streams start at 0
    assert next_chunk_index(call_id, "original") == 0
    assert next_chunk_index(call_id, "final") == 0

    # Verify indices increment properly
    assert next_chunk_index(call_id, "original") == 1
    assert next_chunk_index(call_id, "final") == 1
    assert next_chunk_index(call_id, "original") == 2
    assert next_chunk_index(call_id, "final") == 2


def test_clear_stream_indices():
    """Test that stream indices are properly cleared."""
    call_id = "test-call-2"
    reset_stream_indices(call_id)
    next_chunk_index(call_id, "original")
    next_chunk_index(call_id, "final")

    clear_stream_indices(call_id)

    # After clearing, indices should start fresh
    assert next_chunk_index(call_id, "original") == 0
    assert next_chunk_index(call_id, "final") == 0


def test_build_conversation_events_with_missing_call_id():
    """Test that events are not created without a valid call_id."""
    timestamp = datetime.now(UTC)

    # Test with None call_id
    events = build_conversation_events(
        hook="async_pre_call_hook",
        call_id=None,
        trace_id="trace-1",
        original={"messages": []},
        result=None,
        timestamp_ns_fallback=1234567890,
        timestamp=timestamp,
    )
    assert events == []

    # Test with empty string call_id
    events = build_conversation_events(
        hook="async_pre_call_hook",
        call_id="",
        trace_id="trace-1",
        original={"messages": []},
        result=None,
        timestamp_ns_fallback=1234567890,
        timestamp=timestamp,
    )
    assert events == []


def test_build_conversation_events_pre_call_hook():
    """Test event building for pre-call hook."""
    timestamp = datetime.now(UTC)
    original = {
        "request_data": {"messages": [{"role": "user", "content": "Hello"}]},
        "post_time_ns": 1000000000,
    }
    result = {
        "request_data": {"messages": [{"role": "user", "content": "Hello sanitized"}]},
        "post_time_ns": 1000000001,
    }

    events = build_conversation_events(
        hook="async_pre_call_hook",
        call_id="call-1",
        trace_id="trace-1",
        original=original,
        result=result,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "request_started"
    assert event.call_id == "call-1"
    assert event.trace_id == "trace-1"
    assert event.payload["original_messages"][0]["content"] == "Hello"
    assert event.payload["final_messages"][0]["content"] == "Hello sanitized"


def test_build_conversation_events_stream_chunk():
    """Test event building for streaming chunks."""
    timestamp = datetime.now(UTC)
    original = {
        "response": {"choices": [{"index": 0, "delta": {"content": "Original"}}]},
        "post_time_ns": 1000000000,
    }
    result = {
        "response": {"choices": [{"index": 0, "delta": {"content": "Modified"}}]},
        "post_time_ns": 1000000001,
    }

    reset_stream_indices("call-2")
    events = build_conversation_events(
        hook="async_post_call_streaming_iterator_hook",
        call_id="call-2",
        trace_id="trace-2",
        original=original,
        result=result,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 2
    # Original chunk event
    assert events[0].event_type == "original_chunk"
    assert events[0].payload["delta"] == "Original"
    assert events[0].payload["chunk_index"] == 0
    assert events[0].payload["choice_index"] == 0

    # Final chunk event
    assert events[1].event_type == "final_chunk"
    assert events[1].payload["delta"] == "Modified"
    assert events[1].payload["chunk_index"] == 0
    assert events[1].payload["choice_index"] == 0


def test_build_conversation_events_stream_chunk_missing_choice_index():
    """Test streaming chunk with missing choice index defaults to 0."""
    timestamp = datetime.now(UTC)
    original = {
        "response": {
            "choices": [{"delta": {"content": "No index"}}]  # Missing index field
        },
        "post_time_ns": 1000000000,
    }

    reset_stream_indices("call-3")
    events = build_conversation_events(
        hook="async_post_call_streaming_iterator_hook",
        call_id="call-3",
        trace_id="trace-3",
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    # Should still create event with default choice_index of 0
    assert len(events) == 1
    assert events[0].payload["choice_index"] == 0


def test_build_conversation_events_success_hook():
    """Test event building for successful completion."""
    timestamp = datetime.now(UTC)
    original = {
        "response": {"choices": [{"message": {"content": "Original response"}}]},
        "post_time_ns": 1000000000,
    }
    result = {
        "response": {"choices": [{"message": {"content": "Modified response"}}]},
        "post_time_ns": 1000000001,
    }

    events = build_conversation_events(
        hook="async_post_call_success_hook",
        call_id="call-4",
        trace_id="trace-4",
        original=original,
        result=result,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "request_completed"
    assert event.payload["status"] == "success"
    assert event.payload["original_response"] == "Original response"
    assert event.payload["final_response"] == "Modified response"


def test_build_conversation_events_failure_hook():
    """Test event building for failed requests."""
    timestamp = datetime.now(UTC)
    original = {
        "error": {"message": "Something went wrong"},
        "post_time_ns": 1000000000,
    }

    events = build_conversation_events(
        hook="async_post_call_failure_hook",
        call_id="call-5",
        trace_id="trace-5",
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "request_completed"
    assert event.payload["status"] == "failure"


def test_build_conversation_events_stream_summary():
    """Test event building for streaming summary."""
    timestamp = datetime.now(UTC)
    original = {
        "response": {"choices": [{"message": {"content": "Full response"}}]},
        "post_time_ns": 1000000000,
    }

    events = build_conversation_events(
        hook="async_post_call_streaming_hook",
        call_id="call-6",
        trace_id="trace-6",
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "request_completed"
    assert event.payload["status"] == "stream_summary"
    assert event.payload["final_response"] == "Full response"


def test_build_conversation_events_unknown_hook():
    """Test that unknown hooks produce no events."""
    timestamp = datetime.now(UTC)

    events = build_conversation_events(
        hook="unknown_hook",
        call_id="call-7",
        trace_id="trace-7",
        original={"data": "test"},
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert events == []


def test_build_conversation_events_extract_trace_id_from_original():
    """Test trace_id extraction from original payload."""
    timestamp = datetime.now(UTC)
    original = {
        "request_data": {"messages": [{"role": "user", "content": "Hello"}]},
        "litellm_trace_id": "extracted-trace",
        "post_time_ns": 1000000000,
    }

    events = build_conversation_events(
        hook="async_pre_call_hook",
        call_id="call-8",
        trace_id=None,  # No explicit trace_id
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    assert events[0].trace_id == "extracted-trace"


def test_build_conversation_events_extract_trace_id_from_result():
    """Test trace_id extraction from result payload."""
    timestamp = datetime.now(UTC)
    original = {
        "request_data": {"messages": [{"role": "user", "content": "Hello"}]},
        "post_time_ns": 1000000000,
    }
    result = {
        "request_data": {"messages": [{"role": "user", "content": "Hello"}]},
        "litellm_trace_id": "result-trace",
        "post_time_ns": 1000000001,
    }

    events = build_conversation_events(
        hook="async_pre_call_hook",
        call_id="call-9",
        trace_id=None,  # No explicit trace_id
        original=original,
        result=result,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    assert events[0].trace_id == "result-trace"


def test_build_conversation_events_malformed_response():
    """Test graceful handling of malformed response data."""
    timestamp = datetime.now(UTC)

    # Test with response missing expected structure
    original = {
        "response": "not a dict",  # Malformed response
        "post_time_ns": 1000000000,
    }

    events = build_conversation_events(
        hook="async_post_call_success_hook",
        call_id="call-10",
        trace_id="trace-10",
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    # Should still create event but with empty response text
    assert len(events) == 1
    assert events[0].event_type == "request_completed"
    assert events[0].payload["original_response"] == ""
