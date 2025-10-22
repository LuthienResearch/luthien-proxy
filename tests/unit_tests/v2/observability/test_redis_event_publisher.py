# ABOUTME: Unit tests for redis_event_publisher.py, focusing on pure functions
# ABOUTME: Tests event building logic without requiring Redis infrastructure

"""Unit tests for redis_event_publisher.

These tests focus on the pure function build_activity_event(), which can be tested
without Redis infrastructure. Integration tests cover the async Redis operations.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from luthien_proxy.v2.observability.redis_event_publisher import (
    V2_ACTIVITY_CHANNEL,
    RedisEventPublisher,
    build_activity_event,
    create_event_publisher,
    stream_activity_events,
)


class TestBuildActivityEvent:
    """Test the build_activity_event pure function."""

    def test_minimal_event(self) -> None:
        """Test building an event with only required fields."""
        event = build_activity_event("call-123", "policy.test")

        assert event["call_id"] == "call-123"
        assert event["event_type"] == "policy.test"
        assert "timestamp" in event
        assert "data" not in event

    def test_event_with_data(self) -> None:
        """Test building an event with optional data field."""
        event = build_activity_event(
            "call-123",
            "policy.test",
            data={"key": "value", "count": 42},
        )

        assert event["call_id"] == "call-123"
        assert event["event_type"] == "policy.test"
        assert "timestamp" in event
        assert event["data"] == {"key": "value", "count": 42}

    def test_event_with_none_data(self) -> None:
        """Test that None data is not included in the event."""
        event = build_activity_event("call-123", "policy.test", data=None)

        assert "data" not in event

    def test_event_with_empty_dict_data(self) -> None:
        """Test that empty dict data IS included in the event (truthy check)."""
        event = build_activity_event("call-123", "policy.test", data={})

        # Empty dict is falsy in Python, so it won't be included
        assert "data" not in event

    def test_timestamp_format(self) -> None:
        """Test that timestamp is in ISO 8601 format."""
        event = build_activity_event("call-123", "policy.test")

        timestamp_str = event["timestamp"]
        # Should be parseable as ISO format
        parsed = datetime.fromisoformat(timestamp_str)
        assert parsed.tzinfo is not None  # Should have timezone info

    def test_explicit_timestamp(self) -> None:
        """Test providing an explicit timestamp."""
        explicit_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        event = build_activity_event(
            "call-123",
            "policy.test",
            timestamp=explicit_time,
        )

        assert event["timestamp"] == "2024-01-15T10:30:00+00:00"

    def test_default_timestamp_is_current(self) -> None:
        """Test that default timestamp is approximately now."""
        before = datetime.now(UTC)
        event = build_activity_event("call-123", "policy.test")
        after = datetime.now(UTC)

        timestamp = datetime.fromisoformat(event["timestamp"])
        assert before <= timestamp <= after

    def test_event_type_variations(self) -> None:
        """Test various event type strings."""
        event_types = [
            "policy.content_filtered",
            "request.started",
            "response.completed",
            "error.timeout",
        ]

        for event_type in event_types:
            event = build_activity_event("call-123", event_type)
            assert event["event_type"] == event_type

    def test_call_id_variations(self) -> None:
        """Test various call_id formats."""
        call_ids = [
            "simple-id",
            "uuid-like-089051e6-48eb-de54-4313-3f805f782a49",
            "with_underscores",
            "with-dashes-and_underscores",
        ]

        for call_id in call_ids:
            event = build_activity_event(call_id, "policy.test")
            assert event["call_id"] == call_id

    def test_complex_data_structure(self) -> None:
        """Test event with nested data structure."""
        complex_data = {
            "nested": {"level": 2, "items": [1, 2, 3]},
            "list": ["a", "b", "c"],
            "mixed": {"num": 42, "str": "value", "bool": True, "none": None},
        }

        event = build_activity_event("call-123", "policy.test", data=complex_data)

        assert event["data"] == complex_data
        assert event["data"]["nested"]["items"] == [1, 2, 3]

    def test_data_with_special_characters(self) -> None:
        """Test data containing special characters that need JSON escaping."""
        special_data = {
            "quotes": 'He said "hello"',
            "newlines": "line1\nline2",
            "unicode": "emoji: 🚀",
            "backslash": "path\\to\\file",
        }

        event = build_activity_event("call-123", "policy.test", data=special_data)

        # Verify data is preserved correctly
        assert event["data"]["quotes"] == 'He said "hello"'
        assert event["data"]["newlines"] == "line1\nline2"
        assert event["data"]["unicode"] == "emoji: 🚀"
        assert event["data"]["backslash"] == "path\\to\\file"

    def test_all_fields_present_with_data(self) -> None:
        """Test that all expected fields are present when data is provided."""
        event = build_activity_event(
            "call-123",
            "policy.test",
            data={"key": "value"},
        )

        expected_keys = {"call_id", "event_type", "timestamp", "data"}
        assert set(event.keys()) == expected_keys

    def test_all_fields_present_without_data(self) -> None:
        """Test that all expected fields are present when data is omitted."""
        event = build_activity_event("call-123", "policy.test")

        expected_keys = {"call_id", "event_type", "timestamp"}
        assert set(event.keys()) == expected_keys

    def test_return_type_is_dict(self) -> None:
        """Test that return type is a dict."""
        event = build_activity_event("call-123", "policy.test")

        assert isinstance(event, dict)

    def test_pure_function_no_side_effects(self) -> None:
        """Test that function doesn't mutate input data."""
        input_data = {"key": "value", "nested": {"count": 1}}
        original_data = input_data.copy()

        event = build_activity_event("call-123", "policy.test", data=input_data)

        # Original data should be unchanged
        assert input_data == original_data

        # But event should contain the data
        assert event["data"] == input_data

    @pytest.mark.parametrize(
        "call_id,event_type",
        [
            ("id1", "type1"),
            ("id2", "type2"),
            ("id3", "type3"),
        ],
    )
    def test_parametrized_variations(self, call_id: str, event_type: str) -> None:
        """Test multiple call_id/event_type combinations."""
        event = build_activity_event(call_id, event_type)

        assert event["call_id"] == call_id
        assert event["event_type"] == event_type


# Mock classes for testing stream_activity_events


class FakeRedis:
    """Mock Redis client for testing stream_activity_events."""

    def __init__(self) -> None:
        self.subscribed_channels: list[str] = []
        self.messages: list[dict[str, Any]] = []
        self.message_index = 0

    def pubsub(self) -> "FakePubSub":
        """Return a fake pubsub context manager."""
        return FakePubSub(self)


class FakePubSub:
    """Mock Redis pubsub for testing stream_activity_events."""

    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.subscribed = False

    async def __aenter__(self) -> "FakePubSub":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def subscribe(self, channel: str) -> None:
        """Record subscription."""
        self.redis.subscribed_channels.append(channel)
        self.subscribed = True

    async def get_message(self, ignore_subscribe_messages: bool = False, timeout: float = 1.0) -> dict[str, Any] | None:
        """Return pre-configured messages or None."""
        if self.redis.message_index < len(self.redis.messages):
            msg = self.redis.messages[self.redis.message_index]
            self.redis.message_index += 1
            return msg
        return None


class TestStreamActivityEvents:
    """Test the stream_activity_events async generator."""

    @pytest.mark.asyncio
    async def test_subscribes_to_correct_channel(self) -> None:
        """Test that stream subscribes to the V2_ACTIVITY_CHANNEL."""
        redis = FakeRedis()
        redis.messages = [{"type": "message", "data": b'{"test": "event"}'}]

        chunks = []
        async for chunk in stream_activity_events(redis, heartbeat_seconds=60.0, timeout_seconds=0.1):  # type: ignore
            chunks.append(chunk)
            if len(chunks) >= 1:
                break

        assert V2_ACTIVITY_CHANNEL in redis.subscribed_channels

    @pytest.mark.asyncio
    async def test_yields_sse_formatted_messages(self) -> None:
        """Test that messages are yielded in SSE format."""
        redis = FakeRedis()
        redis.messages = [
            {"type": "message", "data": b'{"event": "test1"}'},
            {"type": "message", "data": b'{"event": "test2"}'},
        ]

        chunks = []
        async for chunk in stream_activity_events(redis, heartbeat_seconds=60.0, timeout_seconds=0.1):  # type: ignore
            chunks.append(chunk)
            if len(chunks) >= 2:
                break

        assert len(chunks) == 2
        assert chunks[0] == 'data: {"event": "test1"}\n\n'
        assert chunks[1] == 'data: {"event": "test2"}\n\n'

    @pytest.mark.asyncio
    async def test_handles_bytes_data(self) -> None:
        """Test that bytes data is properly decoded."""
        redis = FakeRedis()
        redis.messages = [{"type": "message", "data": b"test message bytes"}]

        chunks = []
        async for chunk in stream_activity_events(redis, heartbeat_seconds=60.0, timeout_seconds=0.1):  # type: ignore
            chunks.append(chunk)
            break

        assert chunks[0] == "data: test message bytes\n\n"

    @pytest.mark.asyncio
    async def test_handles_string_data(self) -> None:
        """Test that string data is properly handled."""
        redis = FakeRedis()
        redis.messages = [{"type": "message", "data": "test message string"}]

        chunks = []
        async for chunk in stream_activity_events(redis, heartbeat_seconds=60.0, timeout_seconds=0.1):  # type: ignore
            chunks.append(chunk)
            break

        assert chunks[0] == "data: test message string\n\n"

    @pytest.mark.asyncio
    async def test_sends_heartbeat_when_no_messages(self) -> None:
        """Test that heartbeat events are sent when no messages arrive."""
        redis = FakeRedis()
        redis.messages = []  # No messages

        chunks = []

        async def collect_with_timeout() -> None:
            async for chunk in stream_activity_events(redis, heartbeat_seconds=0.1, timeout_seconds=0.05):  # type: ignore
                chunks.append(chunk)
                if len(chunks) >= 2:  # Collect at least 2 heartbeats
                    break

        # Run with a timeout to avoid infinite loop
        try:
            await asyncio.wait_for(collect_with_timeout(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        # Should have received at least one heartbeat
        assert len(chunks) > 0
        # Heartbeats should be in the correct SSE format
        heartbeat_chunks = [c for c in chunks if c.startswith("event: heartbeat")]
        assert len(heartbeat_chunks) > 0

        # Check heartbeat format
        for heartbeat in heartbeat_chunks:
            assert heartbeat.startswith("event: heartbeat\ndata: ")
            assert heartbeat.endswith("\n\n")
            # Extract and verify JSON data
            data_part = heartbeat.split("data: ")[1].rstrip("\n")
            parsed = json.loads(data_part)
            assert "timestamp" in parsed

    @pytest.mark.asyncio
    async def test_heartbeat_timing(self) -> None:
        """Test that heartbeats are sent at the correct interval."""
        redis = FakeRedis()
        redis.messages = []

        heartbeat_interval = 0.1
        chunks = []

        async def collect_heartbeats() -> None:
            async for chunk in stream_activity_events(
                redis,
                heartbeat_seconds=heartbeat_interval,
                timeout_seconds=0.05,  # type: ignore
            ):
                chunks.append((asyncio.get_event_loop().time(), chunk))
                if len(chunks) >= 3:
                    break

        try:
            await asyncio.wait_for(collect_heartbeats(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        # Filter heartbeat chunks
        heartbeats = [(t, c) for t, c in chunks if "heartbeat" in c]

        # Should have multiple heartbeats
        assert len(heartbeats) >= 2

        # Check intervals between heartbeats (with some tolerance)
        for i in range(1, len(heartbeats)):
            interval = heartbeats[i][0] - heartbeats[i - 1][0]
            # Allow 50% tolerance due to async timing variations
            assert heartbeat_interval * 0.5 <= interval <= heartbeat_interval * 2.0

    @pytest.mark.asyncio
    async def test_ignores_non_message_types(self) -> None:
        """Test that non-message type events are ignored."""
        redis = FakeRedis()
        redis.messages = [
            {"type": "subscribe", "data": "subscription confirmed"},
            {"type": "message", "data": b'{"event": "real_event"}'},
            {"type": "unsubscribe", "data": "unsubscribed"},
        ]

        chunks = []
        async for chunk in stream_activity_events(redis, heartbeat_seconds=60.0, timeout_seconds=0.1):  # type: ignore
            chunks.append(chunk)
            if len(chunks) >= 1:
                break

        # Should only get the actual message, not subscribe/unsubscribe
        assert len(chunks) == 1
        assert 'data: {"event": "real_event"}\n\n' in chunks

    @pytest.mark.asyncio
    async def test_handles_null_message(self) -> None:
        """Test that None messages are handled gracefully."""
        redis = FakeRedis()
        # No messages - get_message will return None
        redis.messages = []

        chunks = []

        async def collect_with_short_timeout() -> None:
            async for chunk in stream_activity_events(redis, heartbeat_seconds=0.2, timeout_seconds=0.05):  # type: ignore
                chunks.append(chunk)
                if len(chunks) >= 1:  # Just get first heartbeat
                    break

        try:
            await asyncio.wait_for(collect_with_short_timeout(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

        # Should get heartbeat even when messages are None
        assert len(chunks) >= 1

    @pytest.mark.asyncio
    async def test_multiple_messages_in_sequence(self) -> None:
        """Test streaming multiple messages in sequence."""
        redis = FakeRedis()
        redis.messages = [
            {"type": "message", "data": b'{"id": 1}'},
            {"type": "message", "data": b'{"id": 2}'},
            {"type": "message", "data": b'{"id": 3}'},
            {"type": "message", "data": b'{"id": 4}'},
            {"type": "message", "data": b'{"id": 5}'},
        ]

        chunks = []
        async for chunk in stream_activity_events(redis, heartbeat_seconds=60.0, timeout_seconds=0.1):  # type: ignore
            chunks.append(chunk)
            if len(chunks) >= 5:
                break

        assert len(chunks) == 5
        for i in range(5):
            assert f'{{"id": {i + 1}}}' in chunks[i]

    @pytest.mark.asyncio
    async def test_json_payload_in_message(self) -> None:
        """Test that JSON payloads are properly forwarded."""
        redis = FakeRedis()
        test_payload = {
            "call_id": "abc123",
            "event_type": "policy.test",
            "timestamp": "2024-01-15T10:30:00Z",
            "data": {"key": "value", "nested": {"count": 42}},
        }
        redis.messages = [{"type": "message", "data": json.dumps(test_payload).encode()}]

        chunks = []
        async for chunk in stream_activity_events(redis, heartbeat_seconds=60.0, timeout_seconds=0.1):  # type: ignore
            chunks.append(chunk)
            break

        # Verify the chunk is in SSE format
        assert chunks[0].startswith("data: ")
        assert chunks[0].endswith("\n\n")

        # Extract and verify the JSON payload
        data_part = chunks[0][6:].rstrip("\n")  # Remove "data: " prefix and trailing newlines
        parsed = json.loads(data_part)

        assert parsed["call_id"] == "abc123"
        assert parsed["event_type"] == "policy.test"
        assert parsed["data"]["key"] == "value"
        assert parsed["data"]["nested"]["count"] == 42

    @pytest.mark.asyncio
    async def test_interleaved_messages_and_heartbeats(self) -> None:
        """Test that messages and heartbeats can be interleaved."""
        redis = FakeRedis()
        # Add one message, then subsequent get_message calls return None (triggering heartbeats)
        redis.messages = [{"type": "message", "data": b'{"event": "test"}'}]

        chunks = []

        async def collect_mixed() -> None:
            async for chunk in stream_activity_events(redis, heartbeat_seconds=0.1, timeout_seconds=0.05):  # type: ignore
                chunks.append(chunk)
                if len(chunks) >= 3:  # Get message + some heartbeats
                    break

        try:
            await asyncio.wait_for(collect_mixed(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        # Should have at least one message and one heartbeat
        message_chunks = [c for c in chunks if "test" in c]
        heartbeat_chunks = [c for c in chunks if "heartbeat" in c]

        assert len(message_chunks) >= 1
        assert len(heartbeat_chunks) >= 1


class TestRedisEventPublisher:
    """Test the RedisEventPublisher class."""

    @pytest.mark.asyncio
    async def test_publish_event_minimal(self) -> None:
        """Test publishing an event with minimal fields."""
        from unittest.mock import AsyncMock, Mock

        mock_redis = Mock()
        mock_redis.publish = AsyncMock()

        publisher = RedisEventPublisher(mock_redis)
        await publisher.publish_event("call-123", "policy.test")

        # Verify Redis publish was called
        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == V2_ACTIVITY_CHANNEL

        # Verify the published message structure
        published_data = json.loads(call_args[0][1])
        assert published_data["call_id"] == "call-123"
        assert published_data["event_type"] == "policy.test"
        assert "timestamp" in published_data
        assert "data" not in published_data

    @pytest.mark.asyncio
    async def test_publish_event_with_data(self) -> None:
        """Test publishing an event with data field."""
        from unittest.mock import AsyncMock, Mock

        mock_redis = Mock()
        mock_redis.publish = AsyncMock()

        publisher = RedisEventPublisher(mock_redis)
        event_data = {"key": "value", "count": 42}
        await publisher.publish_event("call-456", "request.started", data=event_data)

        # Verify Redis publish was called
        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args

        # Verify the published message includes data
        published_data = json.loads(call_args[0][1])
        assert published_data["call_id"] == "call-456"
        assert published_data["event_type"] == "request.started"
        assert published_data["data"] == event_data

    @pytest.mark.asyncio
    async def test_publish_event_handles_redis_failure(self) -> None:
        """Test that publish_event doesn't raise on Redis failures."""
        from unittest.mock import AsyncMock, Mock

        mock_redis = Mock()
        mock_redis.publish = AsyncMock(side_effect=Exception("Redis connection failed"))

        publisher = RedisEventPublisher(mock_redis)

        # Should not raise - failures are logged but not propagated
        await publisher.publish_event("call-789", "error.test")

        # Verify publish was attempted
        mock_redis.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_event_uses_correct_channel(self) -> None:
        """Test that events are published to the correct Redis channel."""
        from unittest.mock import AsyncMock, Mock

        mock_redis = Mock()
        mock_redis.publish = AsyncMock()

        publisher = RedisEventPublisher(mock_redis)
        await publisher.publish_event("call-123", "test.event")

        # Verify channel is correct
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == V2_ACTIVITY_CHANNEL
        assert call_args[0][0] == "luthien:activity"

    @pytest.mark.asyncio
    async def test_publish_event_json_serializable(self) -> None:
        """Test that published events are valid JSON."""
        from unittest.mock import AsyncMock, Mock

        mock_redis = Mock()
        mock_redis.publish = AsyncMock()

        publisher = RedisEventPublisher(mock_redis)
        complex_data = {
            "nested": {"level": 2, "items": [1, 2, 3]},
            "list": ["a", "b", "c"],
            "mixed": {"num": 42, "str": "value", "bool": True, "none": None},
        }
        await publisher.publish_event("call-123", "test.event", data=complex_data)

        # Verify the published data is valid JSON
        call_args = mock_redis.publish.call_args
        published_json = call_args[0][1]
        parsed = json.loads(published_json)

        # Verify structure is preserved
        assert parsed["data"]["nested"]["items"] == [1, 2, 3]
        assert parsed["data"]["mixed"]["bool"] is True
        assert parsed["data"]["mixed"]["none"] is None

    @pytest.mark.asyncio
    async def test_publisher_initialization(self) -> None:
        """Test that publisher stores the Redis client and channel."""
        from unittest.mock import Mock

        mock_redis = Mock()
        publisher = RedisEventPublisher(mock_redis)

        assert publisher.redis is mock_redis
        assert publisher.channel == V2_ACTIVITY_CHANNEL

    @pytest.mark.asyncio
    async def test_multiple_publish_events(self) -> None:
        """Test publishing multiple events in sequence."""
        from unittest.mock import AsyncMock, Mock

        mock_redis = Mock()
        mock_redis.publish = AsyncMock()

        publisher = RedisEventPublisher(mock_redis)

        # Publish multiple events
        await publisher.publish_event("call-1", "event.type1")
        await publisher.publish_event("call-2", "event.type2")
        await publisher.publish_event("call-3", "event.type3")

        # Verify all were published
        assert mock_redis.publish.call_count == 3

        # Verify each call was to the correct channel
        for call in mock_redis.publish.call_args_list:
            assert call[0][0] == V2_ACTIVITY_CHANNEL


class TestCreateEventPublisher:
    """Test the create_event_publisher factory function."""

    @pytest.mark.asyncio
    async def test_create_event_publisher_returns_publisher(self) -> None:
        """Test that create_event_publisher returns a RedisEventPublisher instance."""
        from unittest.mock import AsyncMock, patch

        mock_redis_client = AsyncMock()

        with patch("redis.asyncio.from_url", new=AsyncMock(return_value=mock_redis_client)):
            publisher = await create_event_publisher("redis://localhost:6379")

            assert isinstance(publisher, RedisEventPublisher)
            assert publisher.redis is mock_redis_client

    @pytest.mark.asyncio
    async def test_create_event_publisher_connects_to_redis(self) -> None:
        """Test that create_event_publisher connects to the provided Redis URL."""
        from unittest.mock import AsyncMock, patch

        mock_redis_client = AsyncMock()
        mock_from_url = AsyncMock(return_value=mock_redis_client)

        with patch("redis.asyncio.from_url", new=mock_from_url):
            await create_event_publisher("redis://test-host:1234/0")

            # Verify from_url was called with the correct URL
            mock_from_url.assert_called_once_with("redis://test-host:1234/0")

    @pytest.mark.asyncio
    async def test_create_event_publisher_with_auth(self) -> None:
        """Test create_event_publisher with Redis URL containing auth."""
        from unittest.mock import AsyncMock, patch

        mock_redis_client = AsyncMock()
        mock_from_url = AsyncMock(return_value=mock_redis_client)

        with patch("redis.asyncio.from_url", new=mock_from_url):
            redis_url = "redis://user:password@secure-host:6380/1"
            publisher = await create_event_publisher(redis_url)

            # Verify connection was attempted with auth URL
            mock_from_url.assert_called_once_with(redis_url)
            assert isinstance(publisher, RedisEventPublisher)

    @pytest.mark.asyncio
    async def test_create_event_publisher_channel_configured(self) -> None:
        """Test that created publisher has the correct channel configured."""
        from unittest.mock import AsyncMock, patch

        mock_redis_client = AsyncMock()

        with patch("redis.asyncio.from_url", new=AsyncMock(return_value=mock_redis_client)):
            publisher = await create_event_publisher("redis://localhost:6379")

            assert publisher.channel == V2_ACTIVITY_CHANNEL
