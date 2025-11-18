# ABOUTME: Unit tests for ObservabilityContext implementations
# ABOUTME: Tests NoOpObservabilityContext and DefaultObservabilityContext behavior

from unittest.mock import AsyncMock, Mock, patch

import pytest
from opentelemetry.trace import Span, SpanContext

from luthien_proxy.observability.context import (
    DefaultObservabilityContext,
    NoOpObservabilityContext,
)


class TestNoOpObservabilityContext:
    """Test that NoOpObservabilityContext implements all interface methods as no-ops."""

    @pytest.mark.asyncio
    async def test_emit_event_does_nothing(self):
        """emit_event does nothing and doesn't raise."""
        span = Mock(spec=Span)
        ctx = NoOpObservabilityContext("test-txn-123", span)
        await ctx.emit_event("test.event", {"key": "value"})
        # No assertion - just verify it doesn't raise


class TestDefaultObservabilityContext:
    """Test DefaultObservabilityContext enrichment and delegation."""

    @pytest.mark.asyncio
    async def test_emit_event_enriches_data(self):
        """emit_event enriches data with call_id, timestamp, trace_id, span_id."""
        span = Mock(spec=Span)
        span_context = Mock(spec=SpanContext)
        span_context.trace_id = 123456789
        span_context.span_id = 987654321
        span.get_span_context.return_value = span_context

        ctx = DefaultObservabilityContext("test-txn-789", span)

        with patch("time.time", return_value=1234567890.0):
            await ctx.emit_event("test.event", {"custom": "data"})

        # Verify span.add_event was called with enriched data
        span.add_event.assert_called_once()
        call_args = span.add_event.call_args
        assert call_args[0][0] == "test.event"
        assert call_args[0][1]["custom"] == "data"

    @pytest.mark.asyncio
    async def test_emit_event_calls_db_when_provided(self):
        """emit_event calls DB emit_custom_event when db_pool provided."""
        span = Mock(spec=Span)
        span_context = Mock(spec=SpanContext)
        span_context.trace_id = 123456789
        span_context.span_id = 987654321
        span.get_span_context.return_value = span_context

        db_pool = Mock()
        ctx = DefaultObservabilityContext("test-txn-db", span, db_pool=db_pool)

        with patch(
            "luthien_proxy.storage.events.emit_custom_event",
            new_callable=AsyncMock,
        ) as mock_emit:
            with patch("time.time", return_value=1234567890.0):
                await ctx.emit_event("test.event", {"data": "value"})

            mock_emit.assert_called_once()
            call_args = mock_emit.call_args
            assert call_args[1]["call_id"] == "test-txn-db"
            assert call_args[1]["event_type"] == "test.event"
            assert call_args[1]["data"]["call_id"] == "test-txn-db"
            assert call_args[1]["data"]["timestamp"] == 1234567890.0
            assert call_args[1]["data"]["data"] == "value"

    @pytest.mark.asyncio
    async def test_emit_event_calls_redis_when_provided(self):
        """emit_event calls Redis publish_event when event_publisher provided."""
        span = Mock(spec=Span)
        span_context = Mock(spec=SpanContext)
        span_context.trace_id = 123456789
        span_context.span_id = 987654321
        span.get_span_context.return_value = span_context

        event_publisher = Mock()
        event_publisher.publish_event = AsyncMock()
        ctx = DefaultObservabilityContext("test-txn-redis", span, event_publisher=event_publisher)

        await ctx.emit_event("test.event", {"data": "value"})

        event_publisher.publish_event.assert_called_once_with(
            call_id="test-txn-redis", event_type="test.event", data={"data": "value"}
        )
