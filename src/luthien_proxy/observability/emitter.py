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
import re
import sys
import time
from datetime import UTC, datetime
from typing import Any, Protocol, cast

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

_PREVIEW_MAX_LENGTH = 200
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _extract_session_metadata(
    events: list[DbQueueItem],
) -> tuple[list[str], str | None]:
    """Extract model names and preview message from request events in a batch.

    Returns (models, preview_message). Preview is the first user message text
    from the earliest non-probe request event, or None if none found.
    """
    models: list[str] = []
    preview: str | None = None

    # Sort request events by timestamp to get the earliest first
    request_events = sorted(
        (e for e in events if e[1] == "transaction.request_recorded"),
        key=lambda e: e[3],
    )

    for event in request_events:
        data = event[2]  # safe_data dict

        # Extract model
        model = data.get("final_model")
        if model and model not in models:
            models.append(model)

        # Extract preview from earliest non-probe request
        if preview is None:
            request = data.get("final_request") or data.get("original_request") or {}
            if isinstance(request, dict):
                max_tokens = request.get("max_tokens")
                if max_tokens is not None:
                    try:
                        if int(max_tokens) <= 1:
                            continue  # probe request, skip
                    except (TypeError, ValueError):
                        pass
                messages = request.get("messages", [])
                for msg in messages:
                    if not isinstance(msg, dict) or msg.get("role") != "user":
                        continue
                    content = msg.get("content")
                    if isinstance(content, list):
                        # Anthropic format: list of content blocks
                        texts = [
                            b["text"]
                            for b in content
                            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
                        ]
                        content = " ".join(texts)
                    if isinstance(content, str) and content.strip():
                        text = content.strip()
                        text = _SYSTEM_REMINDER_RE.sub("", text).strip()
                        if not text:
                            continue
                        text = " ".join(text.split())
                        if len(text) > _PREVIEW_MAX_LENGTH:
                            text = text[:_PREVIEW_MAX_LENGTH] + "..."
                        preview = text
                        break

    return models, preview


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
        self._batch_drained: asyncio.Event | None = None
        self._shutting_down: bool = False
        self.dropped_events: int = 0
        self.dropped_db_writes: int = 0
        self._drop_log_interval_s: float = 10.0
        self._last_drop_log: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background drain loop.  Call after the event loop is running."""
        if self._db_pool is not None:
            self._db_queue = asyncio.Queue(maxsize=self._max_queue_size)
            self._batch_drained = asyncio.Event()
            self._drain_task = asyncio.create_task(self._drain_loop())
            self._drain_task.add_done_callback(_log_task_exception)

    async def shutdown(self) -> None:
        """Stop the drain loop and flush remaining events.

        Sets a shutdown flag and waits for the drain loop to finish its
        current batch and drain the queue. Avoids cancel() which would
        drop events that were already collected from the queue but not
        yet written to the DB.
        """
        self._shutting_down = True
        if self._drain_task is not None:
            try:
                await asyncio.wait_for(
                    self._drain_task,
                    timeout=self._shutdown_drain_timeout_s,
                )
            except TimeoutError:
                logger.warning(f"Drain loop did not finish within {self._shutdown_drain_timeout_s}s; cancelling")
                self._drain_task.cancel()
                try:
                    await self._drain_task
                except asyncio.CancelledError:
                    pass

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
        # Clear the drain signal *before* enqueueing so we wait for a drain
        # cycle that includes our event, not one that just finished.
        if self._batch_drained is not None:
            self._batch_drained.clear()
        self.record(transaction_id, event_type, data)
        if self._db_queue is not None and self._batch_drained is not None:
            try:
                await asyncio.wait_for(
                    self._batch_drained.wait(),
                    timeout=self._shutdown_drain_timeout_s,
                )
            except TimeoutError:
                logger.warning(f"emit() timed out waiting for drain after {self._shutdown_drain_timeout_s}s")

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
            task = asyncio.create_task(self._write_events(transaction_id, event_type, safe_data, timestamp))
            task.add_done_callback(_log_task_exception)

        # DB -- enqueue for background batch drain
        if self._db_queue is not None:
            session_id = data.get("session_id") if isinstance(data, dict) else None
            user_hash = data.get("user_hash") if isinstance(data, dict) else None
            try:
                self._db_queue.put_nowait((transaction_id, event_type, safe_data, timestamp, session_id, user_hash))
            except asyncio.QueueFull:
                self.dropped_events += 1
                now = time.monotonic()
                if now - self._last_drop_log >= self._drop_log_interval_s:
                    logger.warning(
                        f"DB write queue full ({self._max_queue_size}), dropped {self.dropped_events} events total"
                    )
                    self._last_drop_log = now

    # ------------------------------------------------------------------
    # Drain loop & batch DB writes
    # ------------------------------------------------------------------

    def _collect_batch(self, max_items: int | None = None) -> list[DbQueueItem]:
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
        """Background task: drain the queue and batch-write to DB.

        Exits after the queue is empty when shutdown is requested, so
        shutdown() doesn't need to cancel mid-write.
        """
        assert self._db_queue is not None  # noqa: S101
        while True:
            if self._shutting_down and self._db_queue.empty():
                return
            try:
                # Wait for the first event (with timeout to allow shutdown checks)
                first = await asyncio.wait_for(self._db_queue.get(), timeout=self._drain_interval_s)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            # Collect more events non-blocking
            batch: list[DbQueueItem] = [first] + self._collect_batch()

            try:
                await self._write_db_batch(batch)
            except Exception as e:
                self.dropped_db_writes += len(batch)
                logger.warning(
                    f"Batch DB write failed ({len(batch)} events dropped, {self.dropped_db_writes} total): {e}",
                    exc_info=True,
                )
            finally:
                if self._batch_drained is not None:
                    self._batch_drained.set()

    async def _write_db_batch(
        self,
        batch: list[DbQueueItem],
    ) -> None:
        """Write a batch of events to the database in a single transaction."""
        db_pool = cast(DatabasePool, self._db_pool)

        async with db_pool.connection() as conn:
            async with conn.transaction():
                # Deduplicate conversation_calls by call_id
                seen_calls: dict[str, tuple[str, datetime, str | None, str | None]] = {}
                for transaction_id, _, _, timestamp, session_id, user_hash in batch:
                    if transaction_id not in seen_calls:
                        seen_calls[transaction_id] = (transaction_id, timestamp, session_id, user_hash)

                # Upsert conversation_calls
                for call_id, ts, sid, uhash in seen_calls.values():
                    await conn.execute(
                        """
                        INSERT INTO conversation_calls (call_id, created_at, session_id, user_hash)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (call_id) DO UPDATE SET
                            session_id = COALESCE(conversation_calls.session_id, EXCLUDED.session_id),
                            user_hash = COALESCE(conversation_calls.user_hash, EXCLUDED.user_hash)
                        """,
                        call_id,
                        ts,
                        sid,
                        uhash,
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

                # Update session_summaries incrementally
                await self._update_session_summaries(conn, batch)

        logger.debug(f"Batch wrote {len(batch)} events to DB")

    @staticmethod
    async def _update_session_summaries(
        conn: Any,
        batch: list[DbQueueItem],
    ) -> None:
        """Incrementally update session_summaries from a batch of events."""
        # Group events by session_id
        session_events: dict[str, list[DbQueueItem]] = {}
        for item in batch:
            sid = item[4]  # session_id
            if sid is not None:
                session_events.setdefault(sid, []).append(item)

        # Fetch existing models for all sessions in this batch in one query
        existing_models_by_session: dict[str, set[str]] = {}
        session_ids = list(session_events.keys())
        if session_ids:
            placeholders = ",".join(f"${i + 1}" for i in range(len(session_ids)))
            rows = await conn.fetch(
                f"SELECT session_id, models_used FROM session_summaries WHERE session_id IN ({placeholders})",
                *session_ids,
            )
            for row in rows:
                csv = row["models_used"]
                if csv:
                    existing_models_by_session[str(row["session_id"])] = {
                        m.strip() for m in str(csv).split(",") if m.strip()
                    }

        for sid, events in session_events.items():
            event_count = len(events)
            call_ids = {e[0] for e in events}  # unique transaction_ids
            policy_count = sum(
                1 for e in events if e[1].startswith("policy.") and not e[1].startswith("policy.judge.evaluation")
            )
            min_ts = min(e[3] for e in events)
            max_ts = max(e[3] for e in events)
            user_hash = next((e[5] for e in events if e[5] is not None), None)

            # Extract models and preview from request events in this batch
            models, preview = _extract_session_metadata(events)

            # Deduplicate new models against any already stored for this session
            existing_models = existing_models_by_session.get(sid)
            if existing_models:
                models = [m for m in models if m not in existing_models]
            models_csv = ",".join(models) if models else None

            # Note: call_count may slightly overcount if a call_id spans
            # multiple batches. Acceptable for an observability summary.
            await conn.execute(
                """
                INSERT INTO session_summaries
                    (session_id, first_seen, last_seen, event_count, call_count,
                     policy_event_count, user_hash, models_used, preview_message)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (session_id) DO UPDATE SET
                    first_seen = LEAST(session_summaries.first_seen, EXCLUDED.first_seen),
                    last_seen = GREATEST(session_summaries.last_seen, EXCLUDED.last_seen),
                    event_count = session_summaries.event_count + EXCLUDED.event_count,
                    call_count = session_summaries.call_count + EXCLUDED.call_count,
                    policy_event_count = session_summaries.policy_event_count + EXCLUDED.policy_event_count,
                    user_hash = COALESCE(session_summaries.user_hash, EXCLUDED.user_hash),
                    models_used = CASE
                        WHEN EXCLUDED.models_used IS NOT NULL THEN
                            COALESCE(session_summaries.models_used || ',' || EXCLUDED.models_used, EXCLUDED.models_used)
                        ELSE session_summaries.models_used
                    END,
                    preview_message = COALESCE(session_summaries.preview_message, EXCLUDED.preview_message)
                """,
                sid,
                min_ts,
                max_ts,
                event_count,
                len(call_ids),
                policy_count,
                user_hash,
                models_csv,
                preview,
            )

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
