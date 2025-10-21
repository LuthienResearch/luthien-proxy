# ABOUTME: Unit tests for PolicyContext
# ABOUTME: Tests event emission and OpenTelemetry span integration

"""Tests for PolicyContext."""

from unittest.mock import Mock

from luthien_proxy.v2.policies.context import PolicyContext


class TestPolicyContext:
    """Test PolicyContext initialization and event emission."""

    def test_create_context(self):
        """Test creating a basic PolicyContext."""
        mock_span = Mock()
        context = PolicyContext(call_id="test-call-123", span=mock_span)

        assert context.call_id == "test-call-123"
        assert context.span == mock_span

    def test_create_context_with_publisher(self):
        """Test creating context with event publisher."""
        mock_span = Mock()
        mock_publisher = Mock()
        context = PolicyContext(call_id="test-call", span=mock_span, event_publisher=mock_publisher)

        assert context._event_publisher == mock_publisher

    def test_emit_basic_event(self):
        """Test emitting basic event adds to span."""
        mock_span = Mock()
        context = PolicyContext(call_id="test-call", span=mock_span)

        context.emit(event_type="policy.test", summary="Test event")

        mock_span.add_event.assert_called_once()
        call_args = mock_span.add_event.call_args
        assert call_args[0][0] == "policy.test"
        assert call_args[1]["attributes"]["event.type"] == "policy.test"
        assert call_args[1]["attributes"]["event.summary"] == "Test event"
        assert call_args[1]["attributes"]["event.severity"] == "info"

    def test_emit_event_with_details(self):
        """Test emitting event with structured details."""
        mock_span = Mock()
        context = PolicyContext(call_id="test-call", span=mock_span)

        context.emit(
            event_type="policy.modified",
            summary="Modified request",
            details={"word_count": 5, "model": "gpt-4"},
        )

        call_args = mock_span.add_event.call_args
        attributes = call_args[1]["attributes"]
        assert attributes["event.word_count"] == 5
        assert attributes["event.model"] == "gpt-4"

    def test_emit_event_with_severity(self):
        """Test emitting event with custom severity."""
        mock_span = Mock()
        context = PolicyContext(call_id="test-call", span=mock_span)

        context.emit(
            event_type="policy.warning",
            summary="Something suspicious",
            severity="warning",
        )

        call_args = mock_span.add_event.call_args
        assert call_args[1]["attributes"]["event.severity"] == "warning"

    def test_emit_converts_complex_types_to_string(self):
        """Test that complex types in details are converted to strings."""
        mock_span = Mock()
        context = PolicyContext(call_id="test-call", span=mock_span)

        context.emit(
            event_type="policy.test",
            summary="Test",
            details={"data": {"nested": "object"}, "list": [1, 2, 3]},
        )

        call_args = mock_span.add_event.call_args
        attributes = call_args[1]["attributes"]
        # Complex types should be stringified
        assert isinstance(attributes["event.data"], str)
        assert isinstance(attributes["event.list"], str)

    def test_emit_preserves_primitive_types(self):
        """Test that primitive types in details are preserved."""
        mock_span = Mock()
        context = PolicyContext(call_id="test-call", span=mock_span)

        context.emit(
            event_type="policy.test",
            summary="Test",
            details={
                "string_val": "hello",
                "int_val": 42,
                "float_val": 3.14,
                "bool_val": True,
            },
        )

        call_args = mock_span.add_event.call_args
        attributes = call_args[1]["attributes"]
        assert attributes["event.string_val"] == "hello"
        assert attributes["event.int_val"] == 42
        assert attributes["event.float_val"] == 3.14
        assert attributes["event.bool_val"] is True

    def test_emit_without_details(self):
        """Test emitting event without details dict."""
        mock_span = Mock()
        context = PolicyContext(call_id="test-call", span=mock_span)

        context.emit(event_type="policy.simple", summary="Simple event")

        call_args = mock_span.add_event.call_args
        attributes = call_args[1]["attributes"]
        # Should only have base attributes
        assert "event.type" in attributes
        assert "event.summary" in attributes
        assert "event.severity" in attributes
        # No extra event.* attributes
        assert len([k for k in attributes if k.startswith("event.")]) == 3
