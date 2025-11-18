# ABOUTME: Observability sinks for writing LuthienRecords to various destinations
# ABOUTME: Each sink knows how to format and write records to a specific backend

"""Observability sinks for multi-destination event emission.

This module provides sink implementations for writing LuthienRecords to:
- Loki (via stdout → Promtail)
- PostgreSQL (for persistent storage)
- Redis (for real-time event streaming)
- OpenTelemetry (for distributed tracing attributes)

Each sink encapsulates its dependencies and formatting logic.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from luthien_proxy.observability.context import LuthienRecord
    from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
    from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


class LuthienRecordSink(ABC):
    """Base class for observability sinks.

    Each sink knows how to write LuthienRecords to a specific destination.
    Sinks are responsible for:
    - Formatting the record appropriately for their destination
    - Handling errors gracefully (log but don't raise)
    - Managing their own dependencies (db_pool, event_publisher, etc.)
    """

    @abstractmethod
    async def write(self, record: LuthienRecord) -> None:
        """Write a LuthienRecord to this sink's destination.

        Args:
            record: The record to write

        Note:
            Implementations should catch and log errors rather than raising,
            to prevent sink failures from breaking the request pipeline.
        """
        pass


class LokiSink(LuthienRecordSink):
    """Sink that writes LuthienRecords to Loki via stdout.

    Uses write_json_to_stdout() to emit JSON logs that Promtail collects.
    """

    async def write(self, record: LuthienRecord) -> None:
        """Write record to stdout for Loki collection."""
        from luthien_proxy.telemetry import write_json_to_stdout

        try:
            # Convert record to dict for JSON emission
            data = vars(record)
            write_json_to_stdout(data)
        except Exception as e:
            logger.warning(f"LokiSink failed to write record: {e}", exc_info=True)


class DatabaseSink(LuthienRecordSink):
    """Sink that writes LuthienRecords to PostgreSQL.

    Persists records to the conversation_events table for long-term storage
    and queryability.
    """

    def __init__(self, db_pool: "DatabasePool | None"):
        """Initialize DatabaseSink.

        Args:
            db_pool: Database pool for PostgreSQL access
        """
        self._db_pool = db_pool

    async def write(self, record: LuthienRecord) -> None:
        """Write record to PostgreSQL."""
        try:
            # TODO: Implement database persistence logic
            # For now, just log that we would write to DB
            logger.debug(
                f"DatabaseSink would write {record.record_type} record "
                f"(transaction_id={getattr(record, 'transaction_id', 'N/A')})"
            )
        except Exception as e:
            logger.warning(f"DatabaseSink failed to write record: {e}", exc_info=True)


class RedisSink(LuthienRecordSink):
    """Sink that writes LuthienRecords to Redis pub/sub.

    Publishes records to Redis channels for real-time event streaming
    to monitoring UIs and other consumers.
    """

    def __init__(self, event_publisher: "RedisEventPublisher | None"):
        """Initialize RedisSink.

        Args:
            event_publisher: Redis event publisher for pub/sub
        """
        self._event_publisher = event_publisher

    async def write(self, record: LuthienRecord) -> None:
        """Write record to Redis pub/sub."""
        try:
            # TODO: Implement Redis pub/sub logic
            # For now, just log that we would publish to Redis
            logger.debug(
                f"RedisSink would publish {record.record_type} record "
                f"(transaction_id={getattr(record, 'transaction_id', 'N/A')})"
            )
        except Exception as e:
            logger.warning(f"RedisSink failed to write record: {e}", exc_info=True)


class OTelSink(LuthienRecordSink):
    """Sink that writes LuthienRecords to OpenTelemetry spans.

    Adds record attributes to the current OTel span for distributed tracing.
    """

    def __init__(self, span: Span):
        """Initialize OTelSink.

        Args:
            span: OpenTelemetry span to add attributes to
        """
        self._span = span

    async def write(self, record: LuthienRecord) -> None:
        """Write record attributes to OTel span."""
        try:
            if not self._span.is_recording():
                return

            # Add record type as span attribute
            self._span.set_attribute(f"luthien.record.{record.record_type}", True)

            # Add transaction_id if present
            if hasattr(record, "transaction_id"):
                self._span.set_attribute("luthien.transaction_id", record.transaction_id)

            # TODO: Add more record-specific attributes as needed
            logger.debug(
                f"OTelSink added {record.record_type} record to span "
                f"(transaction_id={getattr(record, 'transaction_id', 'N/A')})"
            )
        except Exception as e:
            logger.warning(f"OTelSink failed to write record: {e}", exc_info=True)


__all__ = [
    "LuthienRecordSink",
    "LokiSink",
    "DatabaseSink",
    "RedisSink",
    "OTelSink",
]
