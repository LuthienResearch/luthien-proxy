# ABOUTME: Unit tests for V2 control plane models
# ABOUTME: Tests PolicyEvent and StreamingContext

"""Tests for V2 control plane models."""

from datetime import datetime

import pytest

from luthien_proxy.v2.control.models import PolicyEvent, StreamingContext


class TestPolicyEvent:
    """Test PolicyEvent model."""

    def test_create_basic_event(self):
        """Test creating a basic policy event."""
        event = PolicyEvent(
            event_type="test_event",
            call_id="call-123",
            summary="Test event occurred",
        )

        assert event.event_type == "test_event"
        assert event.call_id == "call-123"
        assert event.summary == "Test event occurred"
        assert event.details == {}
        assert event.severity == "info"
        assert isinstance(event.timestamp, datetime)

    def test_create_event_with_details(self):
        """Test creating event with details."""
        event = PolicyEvent(
            event_type="request_modified",
            call_id="call-456",
            summary="Request was modified",
            details={"original_model": "gpt-4", "new_model": "gpt-3.5-turbo", "reason": "cost"},
            severity="warning",
        )

        assert event.event_type == "request_modified"
        assert event.details["original_model"] == "gpt-4"
        assert event.details["new_model"] == "gpt-3.5-turbo"
        assert event.severity == "warning"

    def test_event_timestamp_auto_generated(self):
        """Test that timestamp is auto-generated."""
        # PolicyEvent uses datetime.now() which is naive (no timezone)
        # so we use naive datetimes for comparison
        before = datetime.now()
        event = PolicyEvent(
            event_type="test",
            call_id="call-789",
            summary="Test",
        )
        after = datetime.now()

        # Timestamp should be between before and after
        assert before <= event.timestamp <= after

    def test_event_severity_levels(self):
        """Test different severity levels."""
        severities = ["debug", "info", "warning", "error"]

        for severity in severities:
            event = PolicyEvent(
                event_type="test",
                call_id="call-test",
                summary=f"Test {severity}",
                severity=severity,
            )
            assert event.severity == severity

    def test_event_serialization(self):
        """Test serializing event to dict."""
        event = PolicyEvent(
            event_type="stream_aborted",
            call_id="call-abort",
            summary="Stream was aborted due to policy",
            details={"reason": "content_filter", "chunk_count": 5},
            severity="error",
        )

        data = event.model_dump()
        assert data["event_type"] == "stream_aborted"
        assert data["call_id"] == "call-abort"
        assert data["summary"] == "Stream was aborted due to policy"
        assert data["details"]["reason"] == "content_filter"
        assert data["details"]["chunk_count"] == 5
        assert data["severity"] == "error"
        assert "timestamp" in data

    def test_event_forbids_extra_fields(self):
        """Test that PolicyEvent forbids extra fields."""
        with pytest.raises(Exception):  # Pydantic validation error
            PolicyEvent(
                event_type="test",
                call_id="call-123",
                summary="Test",
                invalid_field="should_fail",
            )

    def test_event_required_fields(self):
        """Test that required fields are enforced."""
        # Missing event_type
        with pytest.raises(Exception):
            PolicyEvent(call_id="call-123", summary="Test")

        # Missing call_id
        with pytest.raises(Exception):
            PolicyEvent(event_type="test", summary="Test")

        # Missing summary
        with pytest.raises(Exception):
            PolicyEvent(event_type="test", call_id="call-123")


class TestStreamingContext:
    """Test StreamingContext model."""

    def test_create_streaming_context(self):
        """Test creating a streaming context."""
        context = StreamingContext(
            stream_id="stream-123",
            call_id="call-456",
        )

        assert context.stream_id == "stream-123"
        assert context.call_id == "call-456"
        assert context.chunk_count == 0

    def test_streaming_context_with_chunk_count(self):
        """Test creating streaming context with chunk count."""
        context = StreamingContext(
            stream_id="stream-789",
            call_id="call-101",
            chunk_count=15,
        )

        assert context.stream_id == "stream-789"
        assert context.call_id == "call-101"
        assert context.chunk_count == 15

    def test_streaming_context_serialization(self):
        """Test serializing streaming context."""
        context = StreamingContext(
            stream_id="stream-abc",
            call_id="call-def",
            chunk_count=42,
        )

        data = context.model_dump()
        assert data["stream_id"] == "stream-abc"
        assert data["call_id"] == "call-def"
        assert data["chunk_count"] == 42

    def test_streaming_context_from_dict(self):
        """Test creating streaming context from dict."""
        data = {
            "stream_id": "stream-xyz",
            "call_id": "call-uvw",
            "chunk_count": 7,
        }

        context = StreamingContext(**data)
        assert context.stream_id == "stream-xyz"
        assert context.call_id == "call-uvw"
        assert context.chunk_count == 7
