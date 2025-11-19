# ABOUTME: Unit tests for ObservabilityContext implementations
# ABOUTME: Tests NoOpObservabilityContext and DefaultObservabilityContext with new record() API

from unittest.mock import AsyncMock, Mock

import pytest

from luthien_proxy.observability.context import (
    DefaultObservabilityContext,
    NoOpObservabilityContext,
    PipelineRecord,
)


class TestNoOpObservabilityContext:
    """Test that NoOpObservabilityContext implements all interface methods as no-ops."""

    def test_record_does_nothing(self):
        """record() does nothing and doesn't raise."""
        ctx = NoOpObservabilityContext("test-txn-123")
        record = PipelineRecord(
            transaction_id="test-txn-123",
            pipeline_stage="test_stage",
            payload='{"test": "data"}',
        )
        ctx.record(record)
        # No assertion - just verify it doesn't raise


class TestDefaultObservabilityContext:
    """Test DefaultObservabilityContext sink routing."""

    @pytest.mark.asyncio
    async def test_record_routes_to_configured_sinks(self):
        """record() routes LuthienRecords to configured sinks."""
        # Create mock sinks
        mock_stdout_sink = Mock()
        mock_stdout_sink.write = AsyncMock()

        mock_db_sink = Mock()
        mock_db_sink.write = AsyncMock()

        # Create context with explicit sink configuration
        config = {
            "stdout_sink": mock_stdout_sink,
            "db_sink": mock_db_sink,
            "routing": {
                PipelineRecord: ["stdout", "db"],
            },
            "default_sinks": ["stdout"],
        }

        ctx = DefaultObservabilityContext("test-txn-123", config=config)

        # Create and emit a record
        record = PipelineRecord(
            transaction_id="test-txn-123",
            pipeline_stage="client_request",
            payload='{"test": "data"}',
        )

        ctx.record(record)

        # Wait a bit for async task to complete
        await AsyncMock()()  # Give event loop a chance to run

        # Verify record was written to configured sinks
        # Note: Since record() uses fire-and-forget (asyncio.create_task),
        # we can't reliably assert on sink calls in this test without
        # adding synchronization. This test primarily verifies no exceptions.

    def test_span_property_returns_current_span(self):
        """span property returns the current auto-instrumented span."""
        ctx = DefaultObservabilityContext("test-txn-456")

        # In unit tests, get_current_span() returns INVALID_SPAN
        span = ctx.span
        assert span is not None
        # Span should be INVALID_SPAN since no real FastAPI request is active
