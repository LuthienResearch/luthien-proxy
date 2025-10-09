"""Test suite for conversation event building (request/response schema)."""

from datetime import UTC, datetime

from luthien_proxy.control_plane.conversation.events import (
    build_conversation_events,
)


def test_build_conversation_events_with_missing_call_id():
    """Test that events are not created without a valid call_id."""
    timestamp = datetime.now(UTC)

    # Test with None call_id
    events = build_conversation_events(
        hook="async_pre_call_hook",
        call_id=None,
        trace_id=None,
        original={"data": {"messages": []}},
        result=None,
        timestamp_ns_fallback=1234567890,
        timestamp=timestamp,
    )
    assert events == []

    # Test with empty string call_id
    events = build_conversation_events(
        hook="async_pre_call_hook",
        call_id="",
        trace_id=None,
        original={"data": {"messages": []}},
        result=None,
        timestamp_ns_fallback=1234567890,
        timestamp=timestamp,
    )
    assert events == []


def test_build_conversation_events_pre_call_hook():
    """Test event building for pre-call hook creates request event."""
    timestamp = datetime.now(UTC)
    original = {
        "data": {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "gpt-4",
            "temperature": 0.7,
        },
        "post_time_ns": 1000000000,
    }
    result = {
        "data": {
            "messages": [{"role": "user", "content": "Hello sanitized"}],
            "model": "gpt-4",
            "temperature": 0.7,
        },
        "post_time_ns": 1000000001,
    }

    events = build_conversation_events(
        hook="async_pre_call_hook",
        call_id="call-1",
        trace_id=None,
        original=original,
        result=result,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "request"
    assert event.call_id == "call-1"
    assert event.trace_id is None
    assert event.payload["model"] == "gpt-4"
    assert event.payload["temperature"] == 0.7
    assert len(event.payload["messages"]) == 1
    assert event.payload["messages"][0]["content"] == "Hello sanitized"


def test_build_conversation_events_success_hook():
    """Test event building for successful completion creates response event."""
    timestamp = datetime.now(UTC)
    original = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Original response"},
                    "finish_reason": "stop",
                }
            ]
        },
        "post_time_ns": 1000000000,
    }

    events = build_conversation_events(
        hook="async_post_call_success_hook",
        call_id="call-2",
        trace_id=None,
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "response"
    assert event.payload["status"] == "success"
    assert event.payload["message"]["content"] == "Original response"
    assert event.payload["finish_reason"] == "stop"


def test_build_conversation_events_failure_hook():
    """Test event building for failed requests."""
    timestamp = datetime.now(UTC)
    original = {
        "error": {"message": "Something went wrong"},
        "post_time_ns": 1000000000,
    }

    events = build_conversation_events(
        hook="async_post_call_failure_hook",
        call_id="call-3",
        trace_id=None,
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "response"
    assert event.payload["status"] == "failure"


def test_build_conversation_events_streaming_hook():
    """Test event building for streaming summary creates response event."""
    timestamp = datetime.now(UTC)
    original = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Streamed response"},
                    "finish_reason": "stop",
                }
            ]
        },
        "post_time_ns": 1000000000,
    }

    events = build_conversation_events(
        hook="async_post_call_streaming_hook",
        call_id="call-4",
        trace_id=None,
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "response"
    assert event.payload["status"] == "success"
    assert event.payload["message"]["content"] == "Streamed response"


def test_build_conversation_events_with_tool_calls():
    """Ensure tool-call responses are stored in message payload."""
    timestamp = datetime.now(UTC)
    original = {
        "response": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_x",
                                "type": "function",
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"query": "status"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        "post_time_ns": 1000000000,
    }

    events = build_conversation_events(
        hook="async_post_call_success_hook",
        call_id="call-tool",
        trace_id=None,
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert len(events) == 1
    payload = events[0].payload
    assert payload["status"] == "success"
    assert "tool_calls" in payload["message"]
    assert payload["message"]["tool_calls"][0]["id"] == "call_x"
    assert payload["message"]["tool_calls"][0]["function"]["name"] == "lookup"


def test_build_conversation_events_unknown_hook():
    """Test that unknown hooks produce no events."""
    timestamp = datetime.now(UTC)

    events = build_conversation_events(
        hook="unknown_hook",
        call_id="call-5",
        trace_id=None,
        original={"data": "test"},
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    assert events == []


def test_build_conversation_events_streaming_chunks_ignored():
    """Test that streaming chunk hooks are ignored (no longer stored)."""
    timestamp = datetime.now(UTC)
    original = {
        "response": {"choices": [{"index": 0, "delta": {"content": "chunk"}}]},
        "post_time_ns": 1000000000,
    }

    events = build_conversation_events(
        hook="async_post_call_streaming_iterator_hook",
        call_id="call-6",
        trace_id=None,
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    # Streaming chunks are no longer stored
    assert events == []


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
        call_id="call-7",
        trace_id=None,
        original=original,
        result=None,
        timestamp_ns_fallback=999999999,
        timestamp=timestamp,
    )

    # Should return no events for malformed data
    assert len(events) == 0
