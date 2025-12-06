"""Event emitter for observability.

Provides a simple interface for recording events to multiple sinks (stdout, db, redis).
Events are also added to the current OTel span as span events.

The EventEmitter should be injected via PolicyContext or Dependencies, not accessed
via global state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from opentelemetry import trace

if TYPE_CHECKING:
    from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
    from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


class EventEmitterProtocol(Protocol):
    """Protocol for event emission.

    This protocol defines the interface that event emitters must implement.
    Use this for type hints when you need to accept any emitter implementation.
    """

    def record(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Record an event (fire-and-forget).

        Args:
            transaction_id: Unique identifier for this transaction
            event_type: Type of event (e.g., "policy.modified_request")
            data: Event payload
        """
        ...


class NullEventEmitter:
    """No-op event emitter for tests or when observability is disabled.

    This implementation silently discards all events, making it safe to use
    in unit tests without any external dependencies.
    """

    def record(
        self,
        transaction_id: str,  # noqa: ARG002
        event_type: str,  # noqa: ARG002
        data: dict[str, Any],  # noqa: ARG002
    ) -> None:
        """Discard the event (no-op)."""
        pass


class EventEmitter:
    """Emits events to multiple sinks: stdout, database, and redis."""

    def __init__(
        self,
        db_pool: "DatabasePool | None" = None,
        redis_publisher: "RedisEventPublisher | None" = None,
        stdout_enabled: bool = True,
    ):
        """Initialize the event emitter with optional sinks.

        Args:
            db_pool: Database pool for persisting events
            redis_publisher: Redis publisher for real-time event streaming
            stdout_enabled: Whether to log events to stdout
        """
        self._db_pool = db_pool
        self._redis_publisher = redis_publisher
        self._stdout_enabled = stdout_enabled

    async def emit(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Emit an event to all configured sinks.

        Args:
            transaction_id: Unique identifier for this transaction
            event_type: Type of event (e.g., "policy.modified_request")
            data: Event payload
        """
        timestamp = datetime.now(UTC)

        # Add to current OTel span as a span event
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(event_type, {"transaction_id": transaction_id, **data})

        # Emit to all sinks concurrently
        tasks = []
        if self._stdout_enabled:
            tasks.append(self._write_stdout(transaction_id, event_type, data, timestamp))
        if self._db_pool:
            tasks.append(self._write_db(transaction_id, event_type, data, timestamp))
        if self._redis_publisher:
            tasks.append(self._write_redis(transaction_id, event_type, data, timestamp))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def record(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Record an event (fire-and-forget).

        This method conforms to EventEmitterProtocol and is the primary interface
        for recording events. It dispatches to emit() in a background task.

        Args:
            transaction_id: Unique identifier for this transaction
            event_type: Type of event (e.g., "policy.modified_request")
            data: Event payload
        """
        asyncio.create_task(self.emit(transaction_id, event_type, data))

    async def _write_stdout(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
        timestamp: datetime,
    ) -> None:
        """Write event to stdout as JSON."""
        try:
            span = trace.get_current_span()
            ctx = span.get_span_context()

            if ctx.is_valid:
                trace_id = format(ctx.trace_id, "032x")
                span_id = format(ctx.span_id, "016x")
            else:
                trace_id = "0" * 32
                span_id = "0" * 16

            log_entry = {
                "timestamp": timestamp.isoformat(),
                "trace_id": trace_id,
                "span_id": span_id,
                "transaction_id": transaction_id,
                "event_type": event_type,
                "data": data,
            }
            print(json.dumps(log_entry), file=sys.stdout, flush=True)
        except Exception as e:
            logger.warning(f"Failed to write event to stdout: {e}", exc_info=True)

    async def _write_db(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
        timestamp: datetime,
    ) -> None:
        """Write event to PostgreSQL."""
        if not self._db_pool:
            return

        try:
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

                    # Lock the call row to serialize concurrent inserts
                    await conn.execute(
                        "SELECT 1 FROM conversation_calls WHERE call_id = $1 FOR UPDATE",
                        transaction_id,
                    )

                    # Insert event with atomically computed sequence number
                    await conn.execute(
                        """
                        INSERT INTO conversation_events (
                            call_id,
                            event_type,
                            sequence,
                            payload,
                            created_at
                        )
                        SELECT $1, $2, COALESCE(MAX(sequence), 0) + 1, $3, $4
                        FROM conversation_events
                        WHERE call_id = $1
                        """,
                        transaction_id,
                        event_type,
                        json.dumps(data),
                        timestamp,
                    )

            logger.debug(f"Wrote event to db: {event_type} (transaction_id={transaction_id})")
        except Exception as e:
            logger.warning(f"Failed to write event to database: {e}", exc_info=True)

    async def _write_redis(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
        timestamp: datetime,  # noqa: ARG002
    ) -> None:
        """Write event to Redis pub/sub."""
        if not self._redis_publisher:
            return

        try:
            await self._redis_publisher.publish_event(
                call_id=transaction_id,
                event_type=event_type,
                data=data,
            )
        except Exception as e:
            logger.warning(f"Failed to write event to redis: {e}", exc_info=True)


__all__ = [
    "EventEmitter",
    "EventEmitterProtocol",
    "NullEventEmitter",
]
