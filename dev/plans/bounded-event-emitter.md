# Bounded EventEmitter: Buffer DB Writes Off the Request Hot Path

**Date:** 2026-04-09
**Status:** Draft
**Problem:** Load investigation Fix 4 / AWS OOM (kernel killed gateway at 1 GB cgroup limit)

## Problem

The `EventEmitter.record()` method creates an unbounded number of `asyncio.Task` objects, each holding a serialized copy of the event payload while waiting to write to the database. Under high event rates (chatty policies like DebugLogging, or `ENABLE_REQUEST_LOGGING=true`), these tasks pile up faster than the DB can drain them. RSS grows monotonically with no steady state.

Evidence: on the AWS `t3.small` deployment (Apr 8), the gateway Python process hit `anon-rss: 1035736kB` (1011 MB) and was OOM-killed by the Docker cgroup limit. The load investigation confirmed RSS growth from 209 MB to 1870 MB at c=32 with DebugLogging — unbounded, never plateauing.

## Design

### Core Concept

Replace unbounded fire-and-forget `asyncio.create_task` per event with:
1. A **bounded `asyncio.Queue(maxsize=10_000)`** for pending DB writes
2. A **single background drain task** that batch-writes events to the database
3. **Stdout and SSE publishing remain inline** in `record()` (they're fast and don't cause memory growth)

### Memory Ceiling

Each serialized event is ~1-3 KB. At 10,000 items, the queue holds ~10-30 MB max. This replaces unbounded growth (1+ GB observed) with a firm ceiling.

### Overflow Policy

When the queue is full, the newest event is dropped. A `dropped_events` counter is incremented and a warning is logged at most once per 10 seconds. This only triggers under extreme load that would otherwise OOM the process. Normal operation never hits the cap.

### New Flow

```
record()
  ├── _safe_serialize(data)          # snapshot data now, before dict can mutate
  ├── _write_stdout(...)             # inline, sync (json.dumps + print)
  ├── asyncio.create_task(publish)   # SSE publish (lightweight, one task)
  └── queue.put_nowait(db_payload)   # bounded, drops if full
                    │
                    ▼
         drain loop (background task)
           ├── wait up to 100ms for first event
           ├── grab up to 50 more events (non-blocking)
           └── batch INSERT to DB
               ├── executemany: conversation_calls upsert
               └── executemany: conversation_events insert
```

### Previous Flow (for comparison)

```
record()
  └── asyncio.create_task(emit())    # UNBOUNDED task creation
        └── emit()
              ├── _safe_serialize(data)
              └── asyncio.gather(stdout, db, sse)  # each task holds full payload
```

### Drain Loop Details

- **Batching**: waits up to 100ms for the first event (avoids busy-spinning), then grabs up to 50 more non-blocking to form a batch.
- **Batch INSERT**: first, deduplicate `conversation_calls` entries by `call_id` within the batch (many events share the same call_id), then one `executemany` for the upserts. Second, one `executemany` for all `conversation_events` inserts. Both wrapped in a single transaction. Works for both Postgres (asyncpg) and SQLite (aiosqlite).
- **Error handling**: if a batch write fails, log the error, increment `dropped_db_writes` by batch size, continue. No retries — observability data isn't worth blocking the drain.
- **Graceful shutdown**: on cancellation, drain remaining queue items with a 5-second timeout. This fixes the "container failed to exit within 2s" issue observed on AWS (fire-and-forget tasks were holding the event loop open).

### `record()` Changes

`record()` stays synchronous (non-async). This is critical — callers (`PolicyContext.record_event()`, pipeline code) call it fire-and-forget without `await`.

The SSE publish path uses a single `asyncio.create_task` for the publisher call. This is bounded by subscriber count (not event count) and was confirmed as non-problematic in the load investigation (10 viewers, <3% throughput impact).

### Startup / Shutdown

- `EventEmitter` gets `start()` and `shutdown()` methods.
- `start()` launches the drain loop background task. Called from `main.py` app startup.
- `shutdown()` cancels the drain loop, then drains remaining items with a timeout. Called from `main.py` app shutdown.
- `NullEventEmitter` does not need start/shutdown (no-op implementation).

### Configuration

Constructor parameters with defaults (no env vars needed):

| Parameter | Default | Purpose |
|---|---|---|
| `max_queue_size` | 10,000 | Bounded queue capacity |
| `batch_size` | 50 | Max events per batch INSERT |
| `drain_interval_ms` | 100 | Max wait for first event in drain loop |
| `shutdown_drain_timeout_s` | 5.0 | Time to drain remaining events on shutdown |

## File Changes

| File | Change |
|---|---|
| `src/luthien_proxy/observability/emitter.py` | Bounded queue, drain loop, batch writes. `record()` does inline stdout + SSE, queued DB. |
| `src/luthien_proxy/main.py` | Call `emitter.start()` on startup, `emitter.shutdown()` on shutdown. |
| `tests/luthien_proxy/unit_tests/observability/test_emitter.py` | Tests for: queue overflow drops, batch writing, graceful shutdown, drain error handling. |

## What Does NOT Change

- `EventEmitterProtocol` — `record()` signature unchanged
- `NullEventEmitter` — stays no-op
- All callers (`PolicyContext`, `anthropic_processor.py`, `dependencies.py`) — they call `record()` which keeps the same signature
- `InProcessEventPublisher` / `RedisEventPublisher` — untouched
- Database schema — no migration needed
- `event_publisher.py` — SSE subscriber queues unchanged

## Success Criteria

1. Gateway RSS stays bounded under DebugLogging at c=32 (target: <400 MB, vs 1870 MB current)
2. No events dropped under normal load (c<=16 with NoOp/Judge policies)
3. Graceful shutdown completes within 5 seconds (vs current "failed to exit within 2s")
4. All existing unit and integration tests pass
5. `dropped_events` counter is observable (logged + available for future metrics)

## Risks

- **Event ordering**: events within a batch are ordered, but batches may interleave with concurrent requests differently than the current per-event writes. This is acceptable for observability data.
- **Staleness**: events are written to DB with a delay of up to ~100ms + batch processing time. The activity SSE stream is unaffected (still inline).
- **`executemany` compatibility**: need to verify both `asyncpg` and `aiosqlite` support the batch INSERT pattern. Both do — `asyncpg.executemany()` and `aiosqlite` wraps sqlite3's `executemany()`.
