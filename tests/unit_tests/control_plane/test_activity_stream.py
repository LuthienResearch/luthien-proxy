"""Test suite for activity stream - global activity event publishing."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.control_plane.activity_stream import (
    ActivityEvent,
    build_activity_events,
    global_activity_channel,
    global_activity_sse_stream,
    publish_activity_event,
)
from luthien_proxy.utils.project_config import ConversationStreamConfig


class FakeRedis:
    """Mock Redis client for testing."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.subscribed_channels: list[str] = []
        self.messages: list[dict[str, Any]] = []
        self.message_index = 0

    async def publish(self, channel: str, message: str) -> None:
        """Record published messages."""
        self.published.append((channel, message))

    def pubsub(self) -> FakePubSub:
        """Return a fake pubsub context manager."""
        return FakePubSub(self)


class FakePubSub:
    """Mock Redis pubsub for testing."""

    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.subscribed = False

    async def __aenter__(self) -> FakePubSub:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def subscribe(self, channel: str) -> None:
        """Record subscription."""
        self.redis.subscribed_channels.append(channel)
        self.subscribed = True

    async def unsubscribe(self, channel: str) -> None:
        """Record unsubscription."""
        self.subscribed = False

    async def get_message(
        self, ignore_subscribe_messages: bool = False, timeout: float = 1.0
    ) -> dict[str, Any] | None:
        """Return pre-configured messages or None."""
        if self.redis.message_index < len(self.redis.messages):
            msg = self.redis.messages[self.redis.message_index]
            self.redis.message_index += 1
            return msg
        return None


# Test ActivityEvent model


def test_activity_event_model_basic():
    """Test ActivityEvent can be created with required fields."""
    event = ActivityEvent(
        timestamp="2024-01-01T00:00:00Z",
        event_type="original_request",
        call_id="call-123",
        summary="Test event",
    )

    assert event.timestamp == "2024-01-01T00:00:00Z"
    assert event.event_type == "original_request"
    assert event.call_id == "call-123"
    assert event.summary == "Test event"
    assert event.trace_id is None
    assert event.hook is None
    assert event.payload == {}


def test_activity_event_model_with_optional_fields():
    """Test ActivityEvent with all optional fields."""
    event = ActivityEvent(
        timestamp="2024-01-01T00:00:00Z",
        event_type="final_response",
        call_id="call-456",
        trace_id="trace-abc",
        hook="async_post_call_success_hook",
        summary="Response modified",
        payload={"content": "Hello", "modified": True},
    )

    assert event.trace_id == "trace-abc"
    assert event.hook == "async_post_call_success_hook"
    assert event.payload == {"content": "Hello", "modified": True}


def test_activity_event_model_forbids_extra_fields():
    """Test that ActivityEvent rejects extra fields."""
    with pytest.raises(Exception):  # Pydantic validation error
        ActivityEvent(
            timestamp="2024-01-01T00:00:00Z",
            event_type="error",
            call_id="call-789",
            summary="Error occurred",
            extra_field="not allowed",  # type: ignore
        )


def test_activity_event_model_dump_json():
    """Test ActivityEvent serialization to JSON."""
    event = ActivityEvent(
        timestamp="2024-01-01T00:00:00Z",
        event_type="original_request",
        call_id="call-123",
        summary="Test",
        payload={"key": "value"},
    )

    json_str = event.model_dump_json()
    parsed = json.loads(json_str)

    assert parsed["timestamp"] == "2024-01-01T00:00:00Z"
    assert parsed["event_type"] == "original_request"
    assert parsed["call_id"] == "call-123"
    assert parsed["summary"] == "Test"
    assert parsed["payload"] == {"key": "value"}


# Test global_activity_channel


def test_global_activity_channel():
    """Test channel name is consistent."""
    channel = global_activity_channel()
    assert channel == "luthien:activity:global"
    assert global_activity_channel() == channel  # Consistent


# Test publish_activity_event


@pytest.mark.asyncio
async def test_publish_activity_event_with_activity_event():
    """Test publishing an ActivityEvent instance."""
    redis = FakeRedis()
    event = ActivityEvent(
        timestamp="2024-01-01T00:00:00Z",
        event_type="original_request",
        call_id="call-123",
        summary="Test",
    )

    await publish_activity_event(redis, event)  # type: ignore

    assert len(redis.published) == 1
    channel, payload = redis.published[0]
    assert channel == "luthien:activity:global"

    parsed = json.loads(payload)
    assert parsed["call_id"] == "call-123"
    assert parsed["event_type"] == "original_request"


@pytest.mark.asyncio
async def test_publish_activity_event_with_dict():
    """Test publishing a plain dict."""
    redis = FakeRedis()
    event = {
        "timestamp": "2024-01-01T00:00:00Z",
        "event_type": "custom",
        "call_id": "call-456",
        "summary": "Custom event",
    }

    await publish_activity_event(redis, event)  # type: ignore

    assert len(redis.published) == 1
    channel, payload = redis.published[0]
    assert channel == "luthien:activity:global"

    parsed = json.loads(payload)
    assert parsed["call_id"] == "call-456"
    assert parsed["event_type"] == "custom"


@pytest.mark.asyncio
async def test_publish_activity_event_serialization_error(caplog: pytest.LogCaptureFixture):
    """Test graceful handling of serialization errors."""
    redis = FakeRedis()

    # Create an object that can't be serialized
    class Unserializable:
        pass

    event = {"data": Unserializable()}  # type: ignore

    await publish_activity_event(redis, event)  # type: ignore

    # Should log error and not crash
    assert len(redis.published) == 0
    assert "Failed to serialize activity event" in caplog.text


@pytest.mark.asyncio
async def test_publish_activity_event_publish_error(caplog: pytest.LogCaptureFixture):
    """Test graceful handling of Redis publish errors."""

    class FailingRedis:
        async def publish(self, channel: str, message: str) -> None:
            raise RuntimeError("Redis connection failed")

    redis = FailingRedis()
    event = ActivityEvent(
        timestamp="2024-01-01T00:00:00Z",
        event_type="error",
        call_id="call-789",
        summary="Error",
    )

    await publish_activity_event(redis, event)  # type: ignore

    # Should log error and not crash
    assert "Failed to publish activity event" in caplog.text


# Test build_activity_events for async_pre_call_hook


def test_build_activity_events_pre_call_hook_basic():
    """Test building events for pre_call hook."""
    original = {"data": {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}}
    result = {"data": {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}}

    events = build_activity_events(
        hook="async_pre_call_hook",
        call_id="call-123",
        trace_id="trace-abc",
        original=original,
        result=result,
    )

    assert len(events) == 2  # original_request + final_request
    assert events[0].event_type == "original_request"
    assert events[0].call_id == "call-123"
    assert events[0].trace_id == "trace-abc"
    assert events[0].hook == "async_pre_call_hook"
    assert "gpt-4" in events[0].summary
    assert events[0].payload["model"] == "gpt-4"
    assert events[0].payload["message_count"] == 1

    assert events[1].event_type == "final_request"
    assert events[1].call_id == "call-123"
    assert events[1].payload["modified"] is False


def test_build_activity_events_pre_call_hook_modified():
    """Test building events when request is modified."""
    original = {
        "data": {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    }
    result = {
        "data": {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello [SANITIZED]"}],
        }
    }

    events = build_activity_events(
        hook="async_pre_call_hook",
        call_id="call-456",
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2
    assert events[1].event_type == "final_request"
    assert events[1].payload["modified"] is True
    assert "[MODIFIED BY POLICY]" in events[1].summary


def test_build_activity_events_pre_call_hook_with_tools():
    """Test building events for request with tools."""
    original = {
        "data": {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [{"type": "function", "function": {"name": "get_weather"}}],
        }
    }
    result = original  # Unchanged

    events = build_activity_events(
        hook="async_pre_call_hook",
        call_id="call-789",
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2
    assert events[0].payload["has_tools"] is True
    assert events[0].payload["tools"] == [{"type": "function", "function": {"name": "get_weather"}}]


def test_build_activity_events_pre_call_hook_missing_call_id():
    """Test building events without call_id."""
    original = {"data": {"model": "gpt-4", "messages": []}}
    result = original

    events = build_activity_events(
        hook="async_pre_call_hook",
        call_id=None,
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2
    assert events[0].call_id == "unknown"
    assert events[1].call_id == "unknown"


def test_build_activity_events_pre_call_hook_malformed_original():
    """Test graceful handling of malformed original data."""
    original = None  # Malformed
    result = {"data": {"model": "gpt-4", "messages": []}}

    events = build_activity_events(
        hook="async_pre_call_hook",
        call_id="call-123",
        trace_id=None,
        original=original,  # type: ignore
        result=result,
    )

    # Should only emit final_request (original extraction failed)
    assert len(events) == 1
    assert events[0].event_type == "final_request"


def test_build_activity_events_pre_call_hook_malformed_result():
    """Test graceful handling of malformed result data."""
    original = {"data": {"model": "gpt-4", "messages": []}}
    result = None  # Malformed

    events = build_activity_events(
        hook="async_pre_call_hook",
        call_id="call-123",
        trace_id=None,
        original=original,
        result=result,  # type: ignore
    )

    # Should only emit original_request (result extraction failed)
    assert len(events) == 1
    assert events[0].event_type == "original_request"


# Test build_activity_events for async_post_call_success_hook


def test_build_activity_events_success_hook_basic():
    """Test building events for successful response."""
    original = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello, how can I help you?"},
                    "finish_reason": "stop",
                }
            ]
        }
    }
    result = original  # Unchanged

    events = build_activity_events(
        hook="async_post_call_success_hook",
        call_id="call-123",
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2  # original_response + final_response
    assert events[0].event_type == "original_response"
    assert events[0].call_id == "call-123"
    assert events[0].payload["content"] == "Hello, how can I help you?"
    assert events[0].payload["content_length"] == 27
    assert events[0].payload["has_tool_calls"] is False

    assert events[1].event_type == "final_response"
    assert events[1].payload["modified"] is False


def test_build_activity_events_success_hook_modified():
    """Test building events when response is modified."""
    original = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Original response"},
                }
            ]
        }
    }
    result = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Modified response"},
                }
            ]
        }
    }

    events = build_activity_events(
        hook="async_post_call_success_hook",
        call_id="call-456",
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2
    assert events[0].payload["content"] == "Original response"
    assert events[1].payload["content"] == "Modified response"
    assert events[1].payload["modified"] is True
    assert "[MODIFIED BY POLICY]" in events[1].summary


def test_build_activity_events_success_hook_with_tool_calls():
    """Test building events for response with tool calls."""
    original = {
        "response": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    }
    result = original

    events = build_activity_events(
        hook="async_post_call_success_hook",
        call_id="call-789",
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2
    assert events[0].payload["has_tool_calls"] is True
    assert events[1].payload["has_tool_calls"] is True


def test_build_activity_events_success_hook_long_content():
    """Test summary truncation for long content."""
    long_content = "x" * 150
    original = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": long_content},
                }
            ]
        }
    }
    result = original

    events = build_activity_events(
        hook="async_post_call_success_hook",
        call_id="call-123",
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2
    # Summary should contain preview (first 100 chars) + "..."
    assert events[0].summary.startswith("Original response: " + "x" * 100)
    assert events[0].summary.endswith("...")
    # Payload should contain full content
    assert events[0].payload["content"] == long_content
    assert events[0].payload["content_length"] == 150


def test_build_activity_events_success_hook_empty_content():
    """Test handling of empty content."""
    original = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": ""},
                }
            ]
        }
    }
    result = original

    events = build_activity_events(
        hook="async_post_call_success_hook",
        call_id="call-123",
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2
    assert events[0].payload["content"] == ""
    assert events[0].payload["content_length"] == 0
    # Should not have "..." suffix for short/empty content
    assert not events[0].summary.endswith("...")


def test_build_activity_events_success_hook_malformed_response():
    """Test graceful handling of malformed response structure."""
    original = {
        "response": "not a dict"  # Malformed
    }
    result = original

    events = build_activity_events(
        hook="async_post_call_success_hook",
        call_id="call-123",
        trace_id=None,
        original=original,  # type: ignore
        result=result,  # type: ignore
    )

    # Should handle gracefully and return empty list
    assert len(events) == 0


def test_build_activity_events_success_hook_missing_choices():
    """Test handling of response without choices."""
    original = {"response": {}}  # No choices
    result = original

    events = build_activity_events(
        hook="async_post_call_success_hook",
        call_id="call-123",
        trace_id=None,
        original=original,
        result=result,
    )

    # Should emit events but with empty content
    assert len(events) == 2
    assert events[0].payload["content"] == ""


# Test build_activity_events for async_post_call_streaming_hook


def test_build_activity_events_streaming_hook_basic():
    """Test building events for streaming response."""
    original = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Streamed response"},
                }
            ]
        }
    }
    result = original

    events = build_activity_events(
        hook="async_post_call_streaming_hook",
        call_id="call-123",
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2
    assert events[0].event_type == "original_response"
    assert "streaming" in events[0].summary.lower()
    assert events[0].payload["streaming"] is True
    assert events[0].payload["content"] == "Streamed response"

    assert events[1].event_type == "final_response"
    assert events[1].payload["streaming"] is True
    assert events[1].payload["modified"] is False


def test_build_activity_events_streaming_hook_modified():
    """Test building events when streaming response is modified."""
    original = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Original stream"},
                }
            ]
        }
    }
    result = {
        "response": {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Modified stream"},
                }
            ]
        }
    }

    events = build_activity_events(
        hook="async_post_call_streaming_hook",
        call_id="call-456",
        trace_id=None,
        original=original,
        result=result,
    )

    assert len(events) == 2
    assert events[1].payload["modified"] is True
    assert "[MODIFIED BY POLICY]" in events[1].summary


# Test build_activity_events for async_post_call_failure_hook


def test_build_activity_events_failure_hook():
    """Test building events for failed request."""
    result = {"error": "Something went wrong"}

    events = build_activity_events(
        hook="async_post_call_failure_hook",
        call_id="call-123",
        trace_id=None,
        original=None,  # type: ignore
        result=result,
    )

    assert len(events) == 1
    assert events[0].event_type == "error"
    assert events[0].call_id == "call-123"
    assert events[0].summary == "Request failed"
    assert "error" in events[0].payload
    # Error payload contains string representation of result
    assert "error" in str(events[0].payload["error"]).lower()


def test_build_activity_events_failure_hook_without_result():
    """Test building events for failure without error details."""
    events = build_activity_events(
        hook="async_post_call_failure_hook",
        call_id="call-456",
        trace_id=None,
        original=None,  # type: ignore
        result=None,  # type: ignore
    )

    assert len(events) == 1
    assert events[0].event_type == "error"
    assert events[0].payload["error"] == "Unknown error"


# Test build_activity_events for unknown hooks


def test_build_activity_events_unknown_hook():
    """Test that unknown hooks produce no events."""
    events = build_activity_events(
        hook="unknown_hook",
        call_id="call-123",
        trace_id=None,
        original={"data": "test"},
        result={"data": "test"},
    )

    assert events == []


# Test global_activity_sse_stream


@pytest.mark.asyncio
async def test_global_activity_sse_stream_basic():
    """Test SSE stream yields data from Redis pubsub."""
    redis = FakeRedis()
    redis.messages = [
        {"data": b'{"event": "test1"}'},
        {"data": b'{"event": "test2"}'},
    ]

    config = ConversationStreamConfig(
        heartbeat_seconds=60.0,  # Long heartbeat to avoid ping
        redis_poll_timeout_seconds=0.1,
    )

    chunks = []
    async for chunk in global_activity_sse_stream(redis, config=config):  # type: ignore
        chunks.append(chunk)
        if len(chunks) == 2:
            break

    assert len(chunks) == 2
    assert chunks[0] == 'data: {"event": "test1"}\n\n'
    assert chunks[1] == 'data: {"event": "test2"}\n\n'
    assert "luthien:activity:global" in redis.subscribed_channels


@pytest.mark.asyncio
async def test_global_activity_sse_stream_heartbeat():
    """Test SSE stream sends heartbeat pings when no messages."""
    redis = FakeRedis()
    redis.messages = []  # No messages

    config = ConversationStreamConfig(
        heartbeat_seconds=0.1,  # Very short heartbeat
        redis_poll_timeout_seconds=0.05,
    )

    chunks = []

    async def collect_with_timeout():
        async for chunk in global_activity_sse_stream(redis, config=config):  # type: ignore
            chunks.append(chunk)
            if len(chunks) >= 2:  # Collect at least 2 pings
                break

    # Run with a timeout to avoid infinite loop
    try:
        await asyncio.wait_for(collect_with_timeout(), timeout=1.0)
    except asyncio.TimeoutError:
        pass

    # Should have received at least one ping
    assert any(chunk == ": ping\n\n" for chunk in chunks)


@pytest.mark.asyncio
async def test_global_activity_sse_stream_handles_bytes():
    """Test SSE stream decodes bytes properly."""
    redis = FakeRedis()
    redis.messages = [{"data": b"test message"}]

    config = ConversationStreamConfig(
        heartbeat_seconds=60.0,
        redis_poll_timeout_seconds=0.1,
    )

    chunks = []
    async for chunk in global_activity_sse_stream(redis, config=config):  # type: ignore
        chunks.append(chunk)
        break

    assert len(chunks) == 1
    assert chunks[0] == "data: test message\n\n"


@pytest.mark.asyncio
async def test_global_activity_sse_stream_handles_string():
    """Test SSE stream handles string data."""
    redis = FakeRedis()
    redis.messages = [{"data": "string message"}]

    config = ConversationStreamConfig(
        heartbeat_seconds=60.0,
        redis_poll_timeout_seconds=0.1,
    )

    chunks = []
    async for chunk in global_activity_sse_stream(redis, config=config):  # type: ignore
        chunks.append(chunk)
        break

    assert len(chunks) == 1
    assert chunks[0] == "data: string message\n\n"
