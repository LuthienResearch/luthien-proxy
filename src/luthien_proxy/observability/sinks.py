# ABOUTME: Observability sinks for writing LuthienRecords to various destinations
# ABOUTME: Each sink knows how to format and write records to a specific backend

"""Observability sinks for multi-destination event emission.

This module provides sink implementations for writing LuthienRecords to:
- Stdout (for collection by log aggregators like Promtail → Loki)
- PostgreSQL (for persistent storage)
- Redis (for real-time event streaming)

Each sink encapsulates its dependencies and formatting logic.
"""

from __future__ import annotations

import json
import logging
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
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


class StdoutSink(LuthienRecordSink):
    """Sink that writes LuthienRecords to stdout as JSON.

    Outputs structured JSON logs with trace context to stdout for collection
    by log aggregators (e.g., Promtail → Loki, Fluent Bit, etc.).
    """

    async def write(self, record: LuthienRecord) -> None:
        """Write record to stdout as JSON with trace context."""
        try:
            # Get current span context for trace correlation
            span = trace.get_current_span()
            ctx = span.get_span_context()

            if ctx.is_valid:
                trace_id = format(ctx.trace_id, "032x")
                span_id = format(ctx.span_id, "016x")
            else:
                trace_id = "0" * 32
                span_id = "0" * 16

            # Build structured log entry
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "trace_id": trace_id,
                "span_id": span_id,
                **vars(record),  # Include all record fields
            }

            # Write to stdout (log aggregators collect this)
            print(json.dumps(log_entry), file=sys.stdout, flush=True)
        except Exception as e:
            logger.warning(f"StdoutSink failed to write record: {e}", exc_info=True)


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
        """Write record to PostgreSQL conversation_events table."""
        if not self._db_pool:
            return

        try:
            transaction_id = getattr(record, "transaction_id", None)
            if not transaction_id:
                logger.debug("DatabaseSink: skipping record without transaction_id")
                return

            # Get record data
            record_data = {k: v for k, v in vars(record).items() if not k.startswith("_")}
            pipeline_stage = record_data.get("pipeline_stage", record.record_type)

            # Determine event_type from pipeline_stage
            if pipeline_stage in ("client_request", "backend_request"):
                event_type = "request"
            elif pipeline_stage in ("client_response", "backend_response"):
                event_type = "response"
            else:
                event_type = pipeline_stage

            timestamp = datetime.now()

            async with self._db_pool.connection() as conn:
                async with conn.transaction():
                    # Ensure call row exists
                    await conn.execute(
                        """
                        INSERT INTO conversation_calls (call_id, created_at)
                        VALUES ($1, $2)
                        ON CONFLICT (call_id) DO NOTHING
                        """,
                        transaction_id,
                        timestamp,
                    )

                    # Get next sequence number for this call
                    next_seq_result = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(sequence), 0) + 1
                        FROM conversation_events
                        WHERE call_id = $1
                        """,
                        transaction_id,
                    )
                    next_sequence = int(next_seq_result) if next_seq_result else 1

                    # Insert event row
                    await conn.execute(
                        """
                        INSERT INTO conversation_events (
                            call_id,
                            event_type,
                            sequence,
                            payload,
                            created_at
                        )
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        transaction_id,
                        event_type,
                        next_sequence,
                        json.dumps(record_data),
                        timestamp,
                    )

            logger.debug(
                f"DatabaseSink wrote {record.record_type} record "
                f"(transaction_id={transaction_id}, event_type={event_type})"
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
        if not self._event_publisher:
            return

        try:
            transaction_id = getattr(record, "transaction_id", "unknown")
            event_type = f"record.{record.record_type}"

            # Build event data from record fields
            data = {k: v for k, v in vars(record).items() if not k.startswith("_")}

            await self._event_publisher.publish_event(
                call_id=transaction_id,
                event_type=event_type,
                data=data,
            )
        except Exception as e:
            logger.warning(f"RedisSink failed to write record: {e}", exc_info=True)


__all__ = [
    "LuthienRecordSink",
    "StdoutSink",
    "DatabaseSink",
    "RedisSink",
]
