# ABOUTME: Unit tests for V2 control plane models
# ABOUTME: Tests StreamingContext and StreamingError

"""Tests for V2 control plane models."""

from luthien_proxy.v2.streaming import StreamingError


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
