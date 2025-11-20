# ABOUTME: Unit tests for observability sinks
# ABOUTME: Tests that sinks properly write records to their destinations

from unittest.mock import AsyncMock, Mock

import pytest

from luthien_proxy.observability.context import PipelineRecord
from luthien_proxy.observability.sinks import RedisSink, StdoutSink


class TestRedisSink:
    """Tests for RedisSink that publishes events to Redis pub/sub."""

    @pytest.mark.asyncio
    async def test_write_publishes_to_redis(self):
        """RedisSink.write() should publish events via RedisEventPublisher."""
        # Create mock event publisher
        mock_publisher = Mock()
        mock_publisher.publish_event = AsyncMock()

        sink = RedisSink(mock_publisher)

        # Create a test record
        record = PipelineRecord(
            transaction_id="test-txn-123",
            pipeline_stage="client_request",
            payload='{"test": "data"}',
        )

        # Write the record
        await sink.write(record)

        # Verify publish_event was called with correct arguments
        mock_publisher.publish_event.assert_called_once()
        call_args = mock_publisher.publish_event.call_args

        assert call_args.kwargs["call_id"] == "test-txn-123"
        assert call_args.kwargs["event_type"] == "record.pipeline"
        assert "transaction_id" in call_args.kwargs["data"]
        assert call_args.kwargs["data"]["pipeline_stage"] == "client_request"

    @pytest.mark.asyncio
    async def test_write_with_no_publisher_does_nothing(self):
        """RedisSink.write() should gracefully handle None publisher."""
        sink = RedisSink(None)

        record = PipelineRecord(
            transaction_id="test-txn-123",
            pipeline_stage="client_request",
            payload='{"test": "data"}',
        )

        # Should not raise
        await sink.write(record)

    @pytest.mark.asyncio
    async def test_write_handles_publisher_errors(self):
        """RedisSink.write() should catch and log errors from publisher."""
        mock_publisher = Mock()
        mock_publisher.publish_event = AsyncMock(side_effect=Exception("Redis connection failed"))

        sink = RedisSink(mock_publisher)

        record = PipelineRecord(
            transaction_id="test-txn-123",
            pipeline_stage="client_request",
            payload='{"test": "data"}',
        )

        # Should not raise even when publisher fails
        await sink.write(record)

    @pytest.mark.asyncio
    async def test_write_excludes_private_fields(self):
        """RedisSink.write() should not include private fields in event data."""
        mock_publisher = Mock()
        mock_publisher.publish_event = AsyncMock()

        sink = RedisSink(mock_publisher)

        record = PipelineRecord(
            transaction_id="test-txn-123",
            pipeline_stage="test",
            payload="{}",
        )

        await sink.write(record)

        # Verify no private fields in data
        call_args = mock_publisher.publish_event.call_args
        data = call_args.kwargs["data"]

        for key in data.keys():
            assert not key.startswith("_"), f"Private field {key} should not be in event data"


class TestStdoutSink:
    """Tests for StdoutSink that writes to stdout."""

    @pytest.mark.asyncio
    async def test_write_outputs_json(self, capsys):
        """StdoutSink.write() should output JSON to stdout."""
        sink = StdoutSink()

        record = PipelineRecord(
            transaction_id="test-txn-123",
            pipeline_stage="client_request",
            payload='{"test": "data"}',
        )

        await sink.write(record)

        captured = capsys.readouterr()
        assert "test-txn-123" in captured.out
        assert "client_request" in captured.out
