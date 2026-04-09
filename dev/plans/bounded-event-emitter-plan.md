# Bounded EventEmitter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unbounded fire-and-forget EventEmitter with a bounded queue + background drain loop that batch-writes to the DB, capping memory growth and preventing OOM crashes.

**Architecture:** `record()` serializes and writes to stdout/SSE inline, then enqueues a lightweight tuple for DB writing. A single background task drains the queue in batches using multi-row INSERTs within a transaction. Queue overflow drops the newest event and increments a counter.

**Tech Stack:** Python asyncio, asyncpg, aiosqlite, pytest

**Spec:** `dev/plans/bounded-event-emitter.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/luthien_proxy/observability/emitter.py` | Modify | Add bounded queue, drain loop, batch DB writes, start/shutdown lifecycle |
| `src/luthien_proxy/main.py` | Modify | Call `emitter.start()` on startup, `emitter.shutdown()` on shutdown |
| `tests/luthien_proxy/unit_tests/observability/test_emitter.py` | Modify | Add tests for new behavior, update existing tests for changed `record()` flow |

---

### Task 1: Add queue infrastructure and overflow test

**Files:**
- Modify: `src/luthien_proxy/observability/emitter.py:121-135`
- Modify: `tests/luthien_proxy/unit_tests/observability/test_emitter.py`

- [ ] **Step 1: Write failing test for queue overflow**

Add to `tests/luthien_proxy/unit_tests/observability/test_emitter.py`:

```python
class TestBoundedEventEmitter:
    """Tests for bounded queue behavior."""

    @pytest.mark.asyncio
    async def test_record_drops_event_when_queue_full(self) -> None:
        """record() should drop events when the DB write queue is full."""
        emitter = EventEmitter(
            db_pool=AsyncMock(),
            stdout_enabled=False,
            max_queue_size=2,
        )
        emitter.start()
        try:
            # Fill the queue
            emitter.record("tx-1", "test.event", {"i": 1})
            emitter.record("tx-2", "test.event", {"i": 2})
            # This one should be dropped
            emitter.record("tx-3", "test.event", {"i": 3})

            assert emitter.dropped_events == 1
            assert emitter._db_queue.qsize() == 2
        finally:
            await emitter.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py::TestBoundedEventEmitter::test_record_drops_event_when_queue_full -v`

Expected: FAIL — `EventEmitter.__init__` doesn't accept `max_queue_size`, no `_db_queue` attribute, no `start()`/`shutdown()`, no `dropped_events`.

- [ ] **Step 3: Add queue, dropped counter, start/shutdown to EventEmitter**

In `src/luthien_proxy/observability/emitter.py`, replace the `EventEmitter.__init__` and add lifecycle methods:

```python
class EventEmitter:
    """Emits events to multiple sinks: stdout, database, and event publisher.

    DB writes are buffered in a bounded queue and flushed by a background
    drain task in batches. Stdout and event-publisher writes happen inline.
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

        self._db_queue: asyncio.Queue[tuple[str, str, dict[str, Any], datetime, str | None, str | None]] | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self.dropped_events: int = 0
        self._drop_log_interval_s: float = 10.0
        self._last_drop_log: float = 0.0

    def start(self) -> None:
        """Start the background drain loop. Call after the event loop is running."""
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
                except (TimeoutError, Exception) as e:
                    logger.warning(f"Failed to drain {len(remaining)} events on shutdown: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py::TestBoundedEventEmitter::test_record_drops_event_when_queue_full -v`

Expected: Still fails — `record()` hasn't been updated to use the queue yet. We'll fix that in the next step.

- [ ] **Step 5: Update `record()` to use the bounded queue**

Replace the existing `record()` method:

```python
    def record(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Record an event to all configured sinks.

        Stdout and event-publisher writes happen inline. DB writes are
        enqueued for the background drain loop. If the queue is full,
        the event is dropped (DB only — stdout and SSE still fire).
        """
        timestamp = datetime.now(UTC)
        safe_data = _safe_serialize(data)

        # OTel span event
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(event_type, {"transaction_id": transaction_id, **safe_data})

        # Stdout — inline, synchronous
        if self._stdout_enabled:
            self._write_stdout_sync(transaction_id, event_type, safe_data, timestamp)

        # Event publisher (SSE) — lightweight fire-and-forget
        if self._event_publisher:
            task = asyncio.create_task(
                self._write_events(transaction_id, event_type, safe_data, timestamp)
            )
            task.add_done_callback(_log_task_exception)

        # DB — enqueue for background batch drain
        if self._db_queue is not None:
            session_id = data.get("session_id") if isinstance(data, dict) else None
            user_hash = data.get("user_hash") if isinstance(data, dict) else None
            try:
                self._db_queue.put_nowait(
                    (transaction_id, event_type, safe_data, timestamp, session_id, user_hash)
                )
            except asyncio.QueueFull:
                self.dropped_events += 1
                now = asyncio.get_event_loop().time()
                if now - self._last_drop_log >= self._drop_log_interval_s:
                    logger.warning(
                        f"DB write queue full ({self._max_queue_size}), "
                        f"dropped {self.dropped_events} events total"
                    )
                    self._last_drop_log = now
```

- [ ] **Step 6: Add synchronous stdout writer**

Add this method to `EventEmitter` (replaces the async version for the inline path):

```python
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
```

- [ ] **Step 7: Run the overflow test**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py::TestBoundedEventEmitter::test_record_drops_event_when_queue_full -v`

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/luthien_proxy/observability/emitter.py tests/luthien_proxy/unit_tests/observability/test_emitter.py
git commit -m "feat: add bounded queue to EventEmitter with overflow drop"
```

---

### Task 2: Implement the drain loop and batch DB writes

**Files:**
- Modify: `src/luthien_proxy/observability/emitter.py`
- Modify: `tests/luthien_proxy/unit_tests/observability/test_emitter.py`

- [ ] **Step 1: Write failing test for batch drain**

Add to `TestBoundedEventEmitter` in the test file:

```python
    @pytest.mark.asyncio
    async def test_drain_loop_writes_batches_to_db(self) -> None:
        """The drain loop should batch-write queued events to the database."""
        mock_conn = AsyncMock()

        @asynccontextmanager
        async def fake_connection():
            yield mock_conn

        mock_pool = AsyncMock()
        mock_pool.connection = fake_connection

        emitter = EventEmitter(
            db_pool=mock_pool,
            stdout_enabled=False,
            max_queue_size=100,
            batch_size=10,
            drain_interval_ms=10,
        )
        emitter.start()
        try:
            # Enqueue 3 events
            emitter.record("tx-1", "test.event", {"i": 1})
            emitter.record("tx-2", "test.event", {"i": 2})
            emitter.record("tx-3", "test.event", {"i": 3})

            # Wait for drain loop to process
            await asyncio.sleep(0.15)

            # DB should have been written to — at least 3 event inserts
            # (calls table upserts + events table inserts)
            assert mock_conn.execute.call_count >= 3
            assert emitter._db_queue.qsize() == 0
        finally:
            await emitter.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py::TestBoundedEventEmitter::test_drain_loop_writes_batches_to_db -v`

Expected: FAIL — `_drain_loop` and `_write_db_batch` not implemented.

- [ ] **Step 3: Implement `_collect_batch` and `_drain_loop`**

Add to `EventEmitter`:

```python
    def _collect_batch(
        self, max_items: int | None = None
    ) -> list[tuple[str, str, dict[str, Any], datetime, str | None, str | None]]:
        """Collect a batch of events from the queue (non-blocking)."""
        if self._db_queue is None:
            return []
        limit = max_items if max_items is not None else self._batch_size
        batch: list[tuple[str, str, dict[str, Any], datetime, str | None, str | None]] = []
        while len(batch) < limit:
            try:
                batch.append(self._db_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _drain_loop(self) -> None:
        """Background task: drain the queue and batch-write to DB."""
        assert self._db_queue is not None
        while True:
            try:
                # Wait for the first event (with timeout to allow cancellation checks)
                first = await asyncio.wait_for(
                    self._db_queue.get(), timeout=self._drain_interval_s
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            # Collect more events non-blocking
            batch = [first] + self._collect_batch()

            try:
                await self._write_db_batch(batch)
            except Exception as e:
                EventEmitter.dropped_db_writes += len(batch)
                logger.warning(
                    f"Batch DB write failed ({len(batch)} events dropped, "
                    f"{EventEmitter.dropped_db_writes} total): {e}",
                    exc_info=True,
                )
```

- [ ] **Step 4: Implement `_write_db_batch`**

Add to `EventEmitter`:

```python
    async def _write_db_batch(
        self,
        batch: list[tuple[str, str, dict[str, Any], datetime, str | None, str | None]],
    ) -> None:
        """Write a batch of events to the database in a single transaction."""
        db_pool = cast(DatabasePool, self._db_pool)

        async with db_pool.connection() as conn:
            async with conn.transaction():
                # Deduplicate conversation_calls by call_id (many events share the same call)
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
                        call_id, ts, sid, uhash,
                    )

                # Insert events
                for transaction_id, event_type, safe_data, timestamp, session_id, _ in batch:
                    await conn.execute(
                        """
                        INSERT INTO conversation_events (call_id, event_type, payload, created_at, session_id)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        transaction_id, event_type, json.dumps(safe_data), timestamp, session_id,
                    )

        logger.debug(f"Batch wrote {len(batch)} events to DB")
```

- [ ] **Step 5: Run the batch drain test**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py::TestBoundedEventEmitter::test_drain_loop_writes_batches_to_db -v`

Expected: PASS

- [ ] **Step 6: Write test for drain loop error handling**

Add to `TestBoundedEventEmitter`:

```python
    @pytest.mark.asyncio
    async def test_drain_loop_continues_after_db_error(self) -> None:
        """The drain loop should keep running after a batch write failure."""
        call_count = 0

        @asynccontextmanager
        async def fake_connection():
            nonlocal call_count
            call_count += 1
            mock_conn = AsyncMock()
            if call_count == 1:
                mock_conn.execute = AsyncMock(
                    side_effect=asyncpg.PostgresError("connection lost")
                )
                mock_conn.transaction = MagicMock(return_value=AsyncMock(
                    __aenter__=AsyncMock(),
                    __aexit__=AsyncMock(return_value=False),
                ))
            yield mock_conn

        mock_pool = AsyncMock()
        mock_pool.connection = fake_connection

        emitter = EventEmitter(
            db_pool=mock_pool,
            stdout_enabled=False,
            max_queue_size=100,
            drain_interval_ms=10,
        )
        before = EventEmitter.dropped_db_writes
        emitter.start()
        try:
            # First event: will fail
            emitter.record("tx-1", "test.event", {"i": 1})
            await asyncio.sleep(0.1)

            # Second event: should succeed (drain loop recovered)
            emitter.record("tx-2", "test.event", {"i": 2})
            await asyncio.sleep(0.1)

            assert EventEmitter.dropped_db_writes > before
            assert emitter._db_queue.qsize() == 0
        finally:
            await emitter.shutdown()
```

- [ ] **Step 7: Run the error handling test**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py::TestBoundedEventEmitter::test_drain_loop_continues_after_db_error -v`

Expected: PASS (the drain loop catches exceptions and continues)

- [ ] **Step 8: Commit**

```bash
git add src/luthien_proxy/observability/emitter.py tests/luthien_proxy/unit_tests/observability/test_emitter.py
git commit -m "feat: implement drain loop with batch DB writes for EventEmitter"
```

---

### Task 3: Graceful shutdown and existing test updates

**Files:**
- Modify: `src/luthien_proxy/observability/emitter.py`
- Modify: `tests/luthien_proxy/unit_tests/observability/test_emitter.py`

- [ ] **Step 1: Write test for graceful shutdown draining**

Add to `TestBoundedEventEmitter`:

```python
    @pytest.mark.asyncio
    async def test_shutdown_drains_remaining_events(self) -> None:
        """shutdown() should flush remaining queued events to DB."""
        written_events: list[str] = []

        @asynccontextmanager
        async def fake_connection():
            mock_conn = AsyncMock()
            original_execute = mock_conn.execute

            async def tracking_execute(query: str, *args: object) -> object:
                if "conversation_events" in query:
                    written_events.append(str(args[0]))  # call_id
                return await original_execute(query, *args)

            mock_conn.execute = tracking_execute
            mock_conn.transaction = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(),
                __aexit__=AsyncMock(return_value=False),
            ))
            yield mock_conn

        mock_pool = AsyncMock()
        mock_pool.connection = fake_connection

        emitter = EventEmitter(
            db_pool=mock_pool,
            stdout_enabled=False,
            max_queue_size=100,
            drain_interval_ms=5000,  # Long interval so drain loop won't fire
        )
        emitter.start()

        # Enqueue events
        emitter.record("tx-1", "test.event", {"i": 1})
        emitter.record("tx-2", "test.event", {"i": 2})

        # Shutdown should drain them
        await emitter.shutdown()

        assert "tx-1" in written_events
        assert "tx-2" in written_events
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py::TestBoundedEventEmitter::test_shutdown_drains_remaining_events -v`

Expected: PASS (the shutdown method already drains remaining items from Task 1)

- [ ] **Step 3: Keep `emit()` for backward compatibility**

Update `emit()` to use the new flow so direct callers (like existing tests) still work:

```python
    async def emit(
        self,
        transaction_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Emit an event to all configured sinks.

        Prefer record() for fire-and-forget usage. This async method is
        kept for backward compatibility and direct-await callers.
        """
        self.record(transaction_id, event_type, data)
        # If DB queue exists, wait for it to drain this event
        if self._db_queue is not None:
            # Give the drain loop a chance to process
            while not self._db_queue.empty():
                await asyncio.sleep(0.01)
```

- [ ] **Step 4: Run all existing emitter tests**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py -v`

Expected: Some existing tests may fail because they mock `emit()` or expect `_write_db` to be called directly from `emit()`. Fix them as needed — the key changes:
- `test_record_creates_background_task` — update to check queue instead of mocking `emit()`
- `test_emit_writes_to_db_sink` — needs the emitter started with a drain loop, or test `_write_db_batch` directly
- `test_emit_writes_to_event_publisher_sink` — should still pass (SSE path unchanged)
- DB error tests — test `_write_db_batch` directly instead of going through `emit()`

- [ ] **Step 5: Update existing tests for new architecture**

Replace the tests that depend on the old `emit() → gather(db, stdout, sse)` flow. The key updates:

For `test_record_creates_background_task`:
```python
    @pytest.mark.asyncio
    async def test_record_enqueues_for_db(self) -> None:
        """record() should enqueue events for the DB drain loop."""
        mock_pool = AsyncMock()
        emitter = EventEmitter(db_pool=mock_pool, stdout_enabled=False)
        emitter.start()
        try:
            emitter.record("tx-123", "test.event", {"key": "value"})
            assert emitter._db_queue.qsize() == 1
        finally:
            await emitter.shutdown()
```

For `test_emit_writes_to_db_sink`:
```python
    @pytest.mark.asyncio
    async def test_write_db_batch_executes_inserts(self) -> None:
        """_write_db_batch should execute conversation_calls upsert and events insert."""
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(),
            __aexit__=AsyncMock(return_value=False),
        ))

        @asynccontextmanager
        async def fake_connection():
            yield mock_conn

        mock_pool = AsyncMock()
        mock_pool.connection = fake_connection

        emitter = EventEmitter(db_pool=mock_pool, stdout_enabled=False)
        timestamp = datetime.now(UTC)

        await emitter._write_db_batch([
            ("tx-1", "test.event", {"key": "value"}, timestamp, None, None),
        ])

        # 1 call upsert + 1 event insert
        assert mock_conn.execute.call_count == 2
```

- [ ] **Step 6: Run all emitter tests**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py -v`

Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/luthien_proxy/observability/emitter.py tests/luthien_proxy/unit_tests/observability/test_emitter.py
git commit -m "feat: update emit() for backward compat, fix existing tests for bounded emitter"
```

---

### Task 4: Wire up start/shutdown in main.py

**Files:**
- Modify: `src/luthien_proxy/main.py:182-292`

- [ ] **Step 1: Add `emitter.start()` after creation**

In `src/luthien_proxy/main.py`, after line 186 (`logger.info("Event emitter created")`), add:

```python
        _emitter.start()
```

- [ ] **Step 2: Add `emitter.shutdown()` in the shutdown block**

In `src/luthien_proxy/main.py`, in the shutdown section (after `yield`, around line 286), add `await _emitter.shutdown()` before the existing cleanup:

```python
        # Shutdown
        await _emitter.shutdown()
        if _telemetry_sender is not None:
            await _telemetry_sender.stop()
```

- [ ] **Step 3: Run full unit test suite**

Run: `uv run pytest tests/luthien_proxy/unit_tests/ -v --timeout=3`

Expected: ALL PASS

- [ ] **Step 4: Run dev_checks**

Run: `"$(git rev-parse --show-toplevel)/scripts/dev_checks.sh"`

Expected: PASS (formatting, linting, type checking, tests)

- [ ] **Step 5: Commit**

```bash
git add src/luthien_proxy/main.py
git commit -m "feat: wire EventEmitter start/shutdown lifecycle in gateway startup"
```

---

### Task 5: Clean up old async methods and unused code

**Files:**
- Modify: `src/luthien_proxy/observability/emitter.py`

- [ ] **Step 1: Remove the old `_write_db` method**

The per-event `_write_db` method is replaced by `_write_db_batch`. Remove `_write_db` entirely from `EventEmitter`. Keep `_write_stdout` (the async version) only if anything still calls it — otherwise remove it too since `_write_stdout_sync` replaces it.

- [ ] **Step 2: Remove the old `_write_stdout` async method**

It's replaced by `_write_stdout_sync`. Remove the async version.

- [ ] **Step 3: Run all tests**

Run: `uv run pytest tests/luthien_proxy/unit_tests/observability/test_emitter.py -v`

Expected: ALL PASS (no test should reference the removed methods)

- [ ] **Step 4: Run dev_checks**

Run: `"$(git rev-parse --show-toplevel)/scripts/dev_checks.sh"`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_proxy/observability/emitter.py
git commit -m "refactor: remove old per-event DB write and async stdout methods"
```

---

### Task 6: Integration smoke test and final validation

**Files:**
- No new files

- [ ] **Step 1: Run the full unit test suite**

Run: `uv run pytest tests/luthien_proxy/unit_tests/ -q`

Expected: ALL PASS

- [ ] **Step 2: Run sqlite e2e tests**

Run: `./scripts/run_e2e.sh sqlite`

Expected: PASS — the gateway starts, events are recorded to the database, the bounded emitter works end-to-end with SQLite.

- [ ] **Step 3: Run dev_checks one final time**

Run: `"$(git rev-parse --show-toplevel)/scripts/dev_checks.sh"`

Expected: PASS

- [ ] **Step 4: Final commit if any formatting fixes**

```bash
git add -A && git commit -m "chore: formatting fixes from dev_checks"
```

(Only if dev_checks made changes.)
