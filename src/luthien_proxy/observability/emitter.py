"""Event emitter for observability.

Provides a simple interface for recording events to multiple sinks (stdout, db, event publisher).
Events are also added to the current OTel span as span events.

DB writes are buffered in a bounded asyncio.Queue and flushed by a single
background drain task in batches.  Stdout and event-publisher writes happen
inline in record().

The EventEmitter should be injected via PolicyContext or Dependencies, not accessed
via global state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import asyncpg
from opentelemetry import trace

from luthien_proxy.observability.event_publisher import EventPublisherProtocol
from luthien_proxy.utils.constants import OTEL_SPAN_ID_HEX_LENGTH, OTEL_TRACE_ID_HEX_LENGTH
from luthien_proxy.utils.db import DatabasePool

# Type alias for the DB queue item tuple
DbQueueItem = tuple[str, str, dict[str, Any], datetime, str | None, str | None]


def _safe_serialize(obj: Any) -> Any:
    """Convert an object to a JSON-serializable form.

    Handles common non-serializable types gracefully:
    - datetime objects -> ISO format strings
    - bytes -> base64-encoded strings (prefixed with "b64:")
    - sets -> lists
    - objects with __dict__ -> their __dict__
    - other non-serializable objects -> their string representation

    Returns a structure that json.dumps() can handle without raising.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj

    if isinstance(obj, datetime):
        return obj.isoformat()

    if isinstance(obj, bytes):
        return f"b64:{base64.b64encode(obj).decode('ascii')}"

    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(item) for item in obj]

    if isinstance(obj, set):
        return [_safe_serialize(item) for item in sorted(obj, key=str)]

    if hasattr(obj, "model_dump"):
        # Pydantic models
        return _safe_serialize(obj.model_dump())

    if hasattr(obj, "__dict__"):
        return _safe_serialize(obj.__dict__)

    # Fallback: convert to string representation
    return str(obj)


logger = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task[None]) -> None:
    """Log exceptions from fire-and-forget tasks to prevent silent failures."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"Exception in background emit task: {exc}", exc_info=exc)


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
    """Emits events to multiple sinks: stdout, database, and event publisher.

    DB writes are buffered in a bounded queue and flushed by a background
    drain task in batches.  Stdout and event-publisher writes happen inline.
    """

    dropped_db_writes: int = 0

    def __init__(
        self,
        db_pool: "DatabasePool | None" = None,
        event_publisher: "EventPublisherProtocol | None" = None,
        stdout_enabled: bool = True,
        max_queue_size: int = 10_000,
        batch_size: int = 50,
        drain_interval_ms: int = 100,
        shutdown_drain_timeout_s: float = 5.0,
    ):
        """Initialize the event emitter with optional sinks."""
        self._db_pool = db_pool
        self._event_publisher = event_publisher
        self._stdout_enabled = stdout_enabled
        self._max_queue_size = max_queue_size
        self._batch_size = batch_size
        self._drain_interval_s = drain_interval_ms / 1000.0
        self._shutdown_drain_timeout_s = shutdown_drain_timeout_s

        self._db_queue: asyncio.Queue[DbQueueItem] | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self.dropped_events: int = 0
        self._drop_log_interval_s: float = 10.0
        self._last_drop_log: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background drain loop.  Call after the event loop is running."""
        if self._db_pool is not None:
            self._db_queue = asyncio.Queue(maxsize=self._max_queue_size)
            self._drain_task = asyncio.create_task(self._drain_loop())
            self._drain_task.add_done_callback(_log_task_exception)

    async def shutdown(self) -> None:
        """Stop the drain loop and flush remaining events."""
        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass

        # Drain remaining items
        if self._db_queue is not None and not self._db_queue.empty():
            remaining = self._collect_batch(max_items=self._db_queue.qsize())
            if remaining:
                try:
                    await asyncio.wait_for(
                        self._write_db_batch(remaining),
                        timeout=self._shutdown_drain_timeout_s,
                    )
                except Exception as e:
                    logger.warning(f"Failed to drain {len(remaining)} events on shutdown: {e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def emit(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Emit an event to all configured sinks.

        Prefer record() for fire-and-forget usage.  This async method is
        kept for backward compatibility and direct-await callers.
        """
        self.record(transaction_id, event_type, data)
        # If DB queue exists, wait for the drain loop to process
        if self._db_queue is not None:
            while not self._db_queue.empty():
                await asyncio.sleep(0.01)

    def record(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Record an event to all configured sinks.

        Stdout and event-publisher writes happen inline.  DB writes are
        enqueued for the background drain loop.  If the queue is full,
        the event is dropped (DB only -- stdout and SSE still fire).
        """
        timestamp = datetime.now(UTC)
        safe_data = _safe_serialize(data)

        # OTel span event
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(event_type, {"transaction_id": transaction_id, **safe_data})

        # Stdout -- inline, synchronous
        if self._stdout_enabled:
            self._write_stdout_sync(transaction_id, event_type, safe_data, timestamp)

        # Event publisher (SSE) -- lightweight fire-and-forget
        if self._event_publisher:
            task = asyncio.create_task(
                self._write_events(transaction_id, event_type, safe_data, timestamp)
            )
            task.add_done_callback(_log_task_exception)

        # DB -- enqueue for background batch drain
        if self._db_queue is not None:
            session_id = data.get("session_id") if isinstance(data, dict) else None
            user_hash = data.get("user_hash") if isinstance(data, dict) else None
            try:
                self._db_queue.put_nowait(
                    (transaction_id, event_type, safe_data, timestamp, session_id, user_hash)
                )
            except asyncio.QueueFull:
                self.dropped_events += 1
                now = time.monotonic()
                if now - self._last_drop_log >= self._drop_log_interval_s:
                    logger.warning(
                        f"DB write queue full ({self._max_queue_size}), "
                        f"dropped {self.dropped_events} events total"
                    )
                    self._last_drop_log = now

    # ------------------------------------------------------------------
    # Drain loop & batch DB writes
    # ------------------------------------------------------------------

    def _collect_batch(
        self, max_items: int | None = None
    ) -> list[DbQueueItem]:
        """Collect a batch of events from the queue (non-blocking)."""
        if self._db_queue is None:
            return []
        limit = max_items if max_items is not None else self._batch_size
        batch: list[DbQueueItem] = []
        while len(batch) < limit:
            try:
                batch.append(self._db_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _drain_loop(self) -> None:
        """Background task: drain the queue and batch-write to DB."""
        assert self._db_queue is not None  # noqa: S101
        while True:
            try:
                # Wait for the first event (with timeout to allow cancellation checks)
                first = await asyncio.wait_for(
                    self._db_queue.get(), timeout=self._drain_interval_s
                )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            # Collect more events non-blocking
            batch: list[DbQueueItem] = [first] + self._collect_batch()

            try:
                await self._write_db_batch(batch)
            except Exception as e:
                EventEmitter.dropped_db_writes += len(batch)
                logger.warning(
                    f"Batch DB write failed ({len(batch)} events dropped, "
                    f"{EventEmitter.dropped_db_writes} total): {e}",
                    exc_info=True,
                )

    async def _write_db_batch(
        self,
        batch: list[DbQueueItem],
    ) -> None:
        """Write a batch of events to the database in a single transaction."""
        db_pool = cast(DatabasePool, self._db_pool)

        async with db_pool.connection() as conn:
            async with conn.transaction():
                # Deduplicate conversation_calls by call_id
                seen_calls: dict[str, tuple[str, datetime, str | None]] = {}
                for transaction_id, _, _, timestamp, session_id, _user_hash in batch:
                    if transaction_id not in seen_calls:
                        seen_calls[transaction_id] = (transaction_id, timestamp, session_id)

                # Upsert conversation_calls
                for call_id, ts, sid in seen_calls.values():
                    await conn.execute(
                        """
                        INSERT INTO conversation_calls (call_id, created_at, session_id)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (call_id) DO UPDATE SET
                            session_id = COALESCE(conversation_calls.session_id, EXCLUDED.session_id)
                        """,
                        call_id,
                        ts,
                        sid,
                    )

                # Insert events
                for transaction_id, event_type, safe_data, timestamp, session_id, _ in batch:
                    await conn.execute(
                        """
                        INSERT INTO conversation_events (call_id, event_type, payload, created_at, session_id)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        transaction_id,
                        event_type,
                        json.dumps(safe_data),
                        timestamp,
                        session_id,
                    )

        logger.debug(f"Batch wrote {len(batch)} events to DB")

    # ------------------------------------------------------------------
    # Inline sink writers
    # ------------------------------------------------------------------

    def _write_stdout_sync(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
        timestamp: datetime,
    ) -> None:
        """Write event to stdout as JSON (synchronous)."""
        try:
            span = trace.get_current_span()
            ctx = span.get_span_context()

            if ctx.is_valid:
                trace_id = format(ctx.trace_id, "032x")
                span_id = format(ctx.span_id, "016x")
            else:
                trace_id = "0" * OTEL_TRACE_ID_HEX_LENGTH
                span_id = "0" * OTEL_SPAN_ID_HEX_LENGTH

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
            logger.warning(f"Failed to write event to stdout: {repr(e)}", exc_info=True)

    async def _write_stdout(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
        timestamp: datetime,
    ) -> None:
        """Write event to stdout as JSON (async, kept for backward compat)."""
        self._write_stdout_sync(transaction_id, event_type, data, timestamp)

    async def _write_db(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
        timestamp: datetime,
    ) -> None:
        """Write event to PostgreSQL (kept for backward compat).

        Session ID Propagation Convention:
            The session_id is extracted from the event data dict if present.
            Callers (e.g., processor.py) should include {"session_id": value}
            in their event data to persist the session_id to the database.
            This convention allows session tracking without modifying the
            EventEmitter interface.
        """
        db_pool = cast(DatabasePool, self._db_pool)
        # Extract session_id and user_hash from data if present (set by processor via convention above)
        session_id = data.get("session_id") if isinstance(data, dict) else None
        user_hash = data.get("user_hash") if isinstance(data, dict) else None

        try:
            async with db_pool.connection() as conn:
                # Ensure call row exists with session_id and user_hash
                await conn.execute(
                    """
                    INSERT INTO conversation_calls (call_id, created_at, session_id, user_hash)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (call_id) DO UPDATE SET
                        session_id = COALESCE(conversation_calls.session_id, EXCLUDED.session_id),
                        user_hash = COALESCE(conversation_calls.user_hash, EXCLUDED.user_hash)
                    """,
                    transaction_id,
                    timestamp,
                    session_id,
                    user_hash,
                )

                # Insert event with session_id, ordering by created_at
                await conn.execute(
                    """
                    INSERT INTO conversation_events (call_id, event_type, payload, created_at, session_id)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    transaction_id,
                    event_type,
                    json.dumps(data),
                    timestamp,
                    session_id,
                )

            logger.debug(f"Wrote event to db: {event_type} (transaction_id={transaction_id})")
        except (OSError, asyncpg.PostgresError, asyncpg.InternalClientError) as e:
            EventEmitter.dropped_db_writes += 1
            logger.warning(
                f"Failed to write event to database ({EventEmitter.dropped_db_writes} total dropped): {repr(e)}",
                exc_info=True,
            )

    async def _write_events(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
        timestamp: datetime,  # noqa: ARG002
    ) -> None:
        """Write event to the event publisher (Redis or in-process)."""
        publisher = cast("EventPublisherProtocol", self._event_publisher)
        try:
            await publisher.publish_event(
                call_id=transaction_id,
                event_type=event_type,
                data=data,
            )
        except Exception as e:
            logger.warning(f"Failed to write event to redis: {repr(e)}", exc_info=True)


__all__ = [
    "EventEmitter",
    "EventEmitterProtocol",
    "NullEventEmitter",
]
