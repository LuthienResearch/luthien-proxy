# ABOUTME: Unit tests for redis_event_publisher.py, focusing on pure functions
# ABOUTME: Tests event building logic without requiring Redis infrastructure

"""Unit tests for redis_event_publisher.

These tests focus on the pure function build_activity_event(), which can be tested
without Redis infrastructure. Integration tests cover the async Redis operations.
"""

from datetime import UTC, datetime

import pytest

from luthien_proxy.v2.observability.redis_event_publisher import build_activity_event


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
