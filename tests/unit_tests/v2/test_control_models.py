# ABOUTME: Unit tests for V2 control plane models
# ABOUTME: Tests StreamingContext and StreamingError

"""Tests for V2 control plane models."""

from luthien_proxy.v2.control.models import StreamingContext, StreamingError


class TestStreamingError:
    """Test StreamingError exception."""

    def test_create_streaming_error(self):
        """Test creating a StreamingError."""
        error = StreamingError("Stream failed")
        assert str(error) == "Stream failed"

    def test_streaming_error_with_cause(self):
        """Test StreamingError with underlying cause."""
        cause = ValueError("Original error")
        error = StreamingError("Stream failed")
        error.__cause__ = cause

        assert str(error) == "Stream failed"
        assert error.__cause__ == cause


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
