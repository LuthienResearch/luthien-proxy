# In-Process Redis Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Redis with in-process Python equivalents when `REDIS_URL` is unset, so the full feature set (including activity monitor) works in local single-process mode without external dependencies.

**Architecture:** Define protocols for each Redis role (event pub/sub, credential cache), with both Redis-backed and in-process implementations. Factory functions in `main.py` select the implementation based on `REDIS_URL`. The policy lock already falls back to `asyncio.Lock` — no changes needed there.

**Tech Stack:** Python 3.13, asyncio, FastAPI, pytest

**Spec:** `docs/superpowers/specs/2026-03-18-in-process-redis-replacement-design.md`

---

## File Structure

**New files:**
- `src/luthien_proxy/observability/event_publisher.py` — `EventPublisherProtocol`, `InProcessEventPublisher`
- `src/luthien_proxy/utils/credential_cache.py` — `CredentialCacheProtocol`, `InProcessCredentialCache`, `RedisCredentialCache`
- `tests/unit_tests/observability/test_in_process_event_publisher.py`
- `tests/unit_tests/utils/test_credential_cache.py`

**Modified files:**
- `src/luthien_proxy/observability/redis_event_publisher.py` — implement `EventPublisherProtocol`, move `stream_activity_events` into class
- `src/luthien_proxy/observability/emitter.py` — accept `EventPublisherProtocol` instead of `RedisEventPublisher`
- `src/luthien_proxy/observability/__init__.py` — export new types
- `src/luthien_proxy/credential_manager.py` — accept `CredentialCacheProtocol` instead of `Redis`
- `src/luthien_proxy/dependencies.py` — replace `redis_client: Redis | None` with `event_publisher` and keep `redis_client` for backward compat
- `src/luthien_proxy/ui/routes.py` — get event publisher from deps instead of raw Redis client
- `src/luthien_proxy/main.py` — create appropriate implementations based on `REDIS_URL`
- `src/luthien_proxy/gateway_routes.py` — in-memory fallback for last_credential_type
- `tests/unit_tests/test_credential_manager.py` — update to use `CredentialCacheProtocol`
- `tests/unit_tests/observability/test_emitter.py` — update to use `EventPublisherProtocol`

---

### Task 1: Event Publisher Protocol + In-Process Implementation

**Files:**
- Create: `src/luthien_proxy/observability/event_publisher.py`
- Create: `tests/unit_tests/observability/test_in_process_event_publisher.py`

- [ ] **Step 1: Write failing tests for InProcessEventPublisher**

Create `tests/unit_tests/observability/test_in_process_event_publisher.py`:

```python
"""Unit tests for in-process event publisher."""

import asyncio
import json

import pytest

from luthien_proxy.observability.event_publisher import InProcessEventPublisher


class TestInProcessEventPublisher:
    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber(self):
        publisher = InProcessEventPublisher()
        received: list[str] = []

        async def consume():
            async for event in publisher.stream_events(heartbeat_seconds=999):
                received.append(event)
                break  # stop after first event

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)  # let consumer register

        await publisher.publish_event("call-1", "test.event", {"key": "value"})
        await asyncio.wait_for(consumer_task, timeout=1.0)

        assert len(received) == 1
        assert "call-1" in received[0]
        assert "test.event" in received[0]

    @pytest.mark.asyncio
    async def test_publish_delivers_to_multiple_subscribers(self):
        publisher = InProcessEventPublisher()
        received_a: list[str] = []
        received_b: list[str] = []

        async def consume(target: list[str]):
            async for event in publisher.stream_events(heartbeat_seconds=999):
                target.append(event)
                break

        task_a = asyncio.create_task(consume(received_a))
        task_b = asyncio.create_task(consume(received_b))
        await asyncio.sleep(0.01)

        await publisher.publish_event("call-1", "test.event")
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=1.0)

        assert len(received_a) == 1
        assert len(received_b) == 1

    @pytest.mark.asyncio
    async def test_no_subscribers_does_not_error(self):
        publisher = InProcessEventPublisher()
        await publisher.publish_event("call-1", "test.event")  # should not raise

    @pytest.mark.asyncio
    async def test_cancelled_subscriber_does_not_receive_events(self):
        """After cancellation, new events are not delivered to the cancelled consumer."""
        publisher = InProcessEventPublisher()
        received: list[str] = []

        async def consume():
            async for event in publisher.stream_events(heartbeat_seconds=999):
                received.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        # Deliver one event, then cancel
        await publisher.publish_event("call-1", "before.cancel")
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Publish after cancel — should not raise, should not deliver
        await publisher.publish_event("call-2", "after.cancel")
        assert len(received) == 1
        assert "before.cancel" in received[0]

    @pytest.mark.asyncio
    async def test_stream_events_produces_sse_format(self):
        publisher = InProcessEventPublisher()
        received: list[str] = []

        async def consume():
            async for event in publisher.stream_events(heartbeat_seconds=999):
                received.append(event)
                break

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        await publisher.publish_event("call-1", "test.event")
        await asyncio.wait_for(task, timeout=1.0)

        assert received[0].startswith("data: ")
        assert received[0].endswith("\n\n")
        payload = json.loads(received[0].removeprefix("data: ").strip())
        assert payload["call_id"] == "call-1"
        assert payload["event_type"] == "test.event"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/observability/test_in_process_event_publisher.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement EventPublisherProtocol and InProcessEventPublisher**

Create `src/luthien_proxy/observability/event_publisher.py`:

```python
"""Event publisher protocol and in-process implementation.

Defines the interface for event publishing (pub/sub) and provides an
in-process implementation using asyncio queues. For local single-process
mode where Redis is not available.

Shared SSE helpers (build_activity_event, format_sse_payload, etc.) live here
so both RedisEventPublisher and InProcessEventPublisher can use them without
importing private symbols across modules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# --- Shared SSE helpers (used by both Redis and in-process publishers) ---


def build_activity_event(
    call_id: str,
    event_type: str,
    data: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build activity event dict for publication."""
    event: dict[str, Any] = {
        "call_id": call_id,
        "event_type": event_type,
        "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
    }
    if data:
        event["data"] = data
    return event


def format_sse_payload(payload: str) -> str:
    return f"data: {payload}\n\n"


def heartbeat_event() -> str:
    return f"event: heartbeat\ndata: {json.dumps({'timestamp': time.time()})}\n\n"


def should_send_heartbeat(last_heartbeat: float, heartbeat_seconds: float) -> bool:
    return time.monotonic() - last_heartbeat >= heartbeat_seconds


# --- Protocol ---


@runtime_checkable
class EventPublisherProtocol(Protocol):
    """Protocol for event publishing and streaming."""

    async def publish_event(
        self,
        call_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None: ...

    def stream_events(
        self,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncGenerator[str, None]:
        """Async generator yielding SSE-formatted event strings."""
        ...


# --- In-process implementation ---


class InProcessEventPublisher:
    """In-process event publisher using asyncio queues.

    For single-process local mode. Each SSE subscriber gets its own queue;
    publish_event pushes to all subscriber queues.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    async def publish_event(
        self,
        call_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        event = build_activity_event(call_id, event_type, data)
        payload = format_sse_payload(json.dumps(event))

        dead_queues: list[asyncio.Queue[str]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead_queues.append(queue)
                logger.warning("Dropping slow event subscriber")

        for q in dead_queues:
            self._subscribers.discard(q)

    async def stream_events(
        self,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        last_heartbeat = time.monotonic()

        try:
            while True:
                if should_send_heartbeat(last_heartbeat, heartbeat_seconds):
                    last_heartbeat = time.monotonic()
                    yield heartbeat_event()
                    continue

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            self._subscribers.discard(queue)
```

**Note:** The shared helpers (`build_activity_event`, `format_sse_payload`, `heartbeat_event`, `should_send_heartbeat`) are moved here from `redis_event_publisher.py`. Task 2 will update `redis_event_publisher.py` to import them from here instead of defining them locally. The `stream_events` protocol method is declared as a plain `def` (not `async def`) because it's an async generator — it yields values, not returns them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/observability/test_in_process_event_publisher.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_proxy/observability/event_publisher.py tests/unit_tests/observability/test_in_process_event_publisher.py
git commit -m "feat: add EventPublisherProtocol and InProcessEventPublisher"
```

---

### Task 2: Make RedisEventPublisher Implement the Protocol

**Files:**
- Modify: `src/luthien_proxy/observability/redis_event_publisher.py`
- Modify: `tests/unit_tests/observability/test_redis_event_publisher.py`

- [ ] **Step 1: Update `redis_event_publisher.py` to import shared helpers from `event_publisher.py`**

Replace the local definitions of `build_activity_event`, `_format_sse_payload`, `_heartbeat_event`, `_should_send_heartbeat` with imports from `event_publisher`:

```python
from luthien_proxy.observability.event_publisher import (
    build_activity_event,
    format_sse_payload,
    heartbeat_event,
    should_send_heartbeat,
)
```

Update all references in `stream_activity_events` to use the new public names (no underscore prefix): `format_sse_payload`, `heartbeat_event`, `should_send_heartbeat`. Also update `_decode_payload` → `format_sse_payload` call if applicable.

- [ ] **Step 2: Add `stream_events` method to `RedisEventPublisher`**

Add a `stream_events` method that wraps the existing `stream_activity_events` standalone function, so `RedisEventPublisher` satisfies `EventPublisherProtocol`:

```python
async def stream_events(
    self,
    heartbeat_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncGenerator[str, None]:
    """Stream activity events as SSE. Satisfies EventPublisherProtocol."""
    async for event in stream_activity_events(
        self.redis,
        heartbeat_seconds=heartbeat_seconds,
    ):
        yield event
```

- [ ] **Step 3: Update tests to use new import paths**

In `test_redis_event_publisher.py`, update any imports of `build_activity_event` to come from `event_publisher` module. Update references to the renamed helpers.

- [ ] **Step 4: Run existing publisher tests to verify no regressions**

Run: `uv run pytest tests/unit_tests/observability/test_redis_event_publisher.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_proxy/observability/redis_event_publisher.py src/luthien_proxy/observability/event_publisher.py tests/unit_tests/observability/test_redis_event_publisher.py
git commit -m "refactor: move shared SSE helpers to event_publisher, add stream_events to RedisEventPublisher"
```

---

### Task 3: Credential Cache Protocol + In-Process Implementation

**Files:**
- Create: `src/luthien_proxy/utils/credential_cache.py`
- Create: `tests/unit_tests/utils/test_credential_cache.py`

- [ ] **Step 1: Write failing tests for InProcessCredentialCache**

Create `tests/unit_tests/utils/test_credential_cache.py`:

```python
"""Unit tests for in-process credential cache."""

import asyncio
import time

import pytest

from luthien_proxy.utils.credential_cache import InProcessCredentialCache


class TestInProcessCredentialCache:
    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing_key(self):
        cache = InProcessCredentialCache()
        assert await cache.get("missing") is None

    @pytest.mark.asyncio
    async def test_setex_and_get(self):
        cache = InProcessCredentialCache()
        await cache.setex("key1", 60, "value1")
        assert await cache.get("key1") == "value1"

    @pytest.mark.asyncio
    async def test_expired_key_returns_none(self):
        cache = InProcessCredentialCache()
        await cache.setex("key1", 0, "value1")  # expires immediately
        await asyncio.sleep(0.01)
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_delete_removes_key(self):
        cache = InProcessCredentialCache()
        await cache.setex("key1", 60, "value1")
        result = await cache.delete("key1")
        assert result is True
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_delete_missing_key_returns_false(self):
        cache = InProcessCredentialCache()
        result = await cache.delete("missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_ttl_returns_remaining_seconds(self):
        cache = InProcessCredentialCache()
        await cache.setex("key1", 60, "value1")
        remaining = await cache.ttl("key1")
        assert 58 <= remaining <= 60

    @pytest.mark.asyncio
    async def test_ttl_missing_key_returns_negative(self):
        cache = InProcessCredentialCache()
        assert await cache.ttl("missing") == -2

    @pytest.mark.asyncio
    async def test_scan_iter_yields_matching_keys(self):
        cache = InProcessCredentialCache()
        await cache.setex("prefix:a", 60, "va")
        await cache.setex("prefix:b", 60, "vb")
        await cache.setex("other:c", 60, "vc")

        keys = [k async for k in cache.scan_iter(match="prefix:*")]
        assert set(keys) == {"prefix:a", "prefix:b"}

    @pytest.mark.asyncio
    async def test_scan_iter_skips_expired(self):
        cache = InProcessCredentialCache()
        await cache.setex("prefix:a", 0, "va")  # expired
        await cache.setex("prefix:b", 60, "vb")
        await asyncio.sleep(0.01)

        keys = [k async for k in cache.scan_iter(match="prefix:*")]
        assert keys == ["prefix:b"]

    @pytest.mark.asyncio
    async def test_unlink_bulk_deletes(self):
        cache = InProcessCredentialCache()
        await cache.setex("a", 60, "va")
        await cache.setex("b", 60, "vb")
        await cache.setex("c", 60, "vc")

        count = await cache.unlink("a", "b")
        assert count == 2
        assert await cache.get("a") is None
        assert await cache.get("c") == "vc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/utils/test_credential_cache.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement CredentialCacheProtocol, InProcessCredentialCache, and RedisCredentialCache**

Create `src/luthien_proxy/utils/credential_cache.py`:

```python
"""Credential cache protocol and implementations.

Provides a protocol for TTL key-value caching used by CredentialManager,
with both Redis-backed and in-process implementations.
"""

from __future__ import annotations

import fnmatch
import time
from typing import AsyncIterator, Protocol, runtime_checkable

import redis.asyncio as redis


@runtime_checkable
class CredentialCacheProtocol(Protocol):
    """Protocol for credential validation caching with TTL support."""

    async def get(self, key: str) -> str | None: ...
    async def setex(self, key: str, ttl: int, value: str) -> None: ...
    async def delete(self, key: str) -> bool: ...
    async def ttl(self, key: str) -> int: ...
    async def scan_iter(self, *, match: str) -> AsyncIterator[str]: ...
    async def unlink(self, *keys: str) -> int: ...


class InProcessCredentialCache:
    """In-process TTL cache for single-process local mode.

    Stores entries as (value, expiry_timestamp). Expired entries are
    cleaned up lazily on read and during scan.
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._data[key]
            return None
        return value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None

    async def ttl(self, key: str) -> int:
        entry = self._data.get(key)
        if entry is None:
            return -2
        _, expires_at = entry
        remaining = int(expires_at - time.monotonic())
        if remaining <= 0:
            del self._data[key]
            return -2
        return remaining

    async def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        now = time.monotonic()
        expired: list[str] = []
        for key, (_, expires_at) in self._data.items():
            if now >= expires_at:
                expired.append(key)
                continue
            if fnmatch.fnmatch(key, match):
                yield key
        for key in expired:
            del self._data[key]

    async def unlink(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if self._data.pop(key, None) is not None:
                count += 1
        return count


class RedisCredentialCache:
    """Redis-backed credential cache. Thin wrapper matching the protocol."""

    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    async def get(self, key: str) -> str | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return raw if isinstance(raw, str) else raw.decode()

    async def setex(self, key: str, ttl: int, value: str) -> None:
        await self._redis.setex(key, ttl, value)

    async def delete(self, key: str) -> bool:
        return (await self._redis.delete(key)) > 0

    async def ttl(self, key: str) -> int:
        return await self._redis.ttl(key)

    async def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        async for key in self._redis.scan_iter(match=match):
            yield key if isinstance(key, str) else key.decode()

    async def unlink(self, *keys: str) -> int:
        return int(await self._redis.unlink(*keys))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/utils/test_credential_cache.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_proxy/utils/credential_cache.py tests/unit_tests/utils/test_credential_cache.py
git commit -m "feat: add CredentialCacheProtocol with in-process and Redis implementations"
```

---

### Task 4: Wire CredentialManager to Use CredentialCacheProtocol

**Files:**
- Modify: `src/luthien_proxy/credential_manager.py`
- Modify: `tests/unit_tests/test_credential_manager.py`

- [ ] **Step 1: Update CredentialManager to accept CredentialCacheProtocol**

In `credential_manager.py`:

1. Replace `from redis.asyncio import Redis` with `from luthien_proxy.utils.credential_cache import CredentialCacheProtocol`

2. Change `__init__` signature:
```python
def __init__(self, db_pool: DatabasePool | None, cache: CredentialCacheProtocol | None):
    self._db_pool = db_pool
    self._cache = cache
    # ... rest unchanged
```

3. Replace all `self._redis` references with `self._cache` throughout the file. The method names (`get`, `setex`, `delete`, `scan_iter`, `unlink`, `ttl`) already match the protocol — this is a direct rename.

Specific changes in internal helpers:
- `_get_cached`: `self._cache.get(...)` instead of `self._redis.get(...)`
- `_cache_result`: `self._cache.setex(...)` instead of `self._redis.setex(...)`
- `_touch_last_used`: `self._cache.get(...)`, `self._cache.ttl(...)`, `self._cache.setex(...)`
- `_invalidate_key`: `self._cache.delete(...)` instead of `self._redis.delete(...)`
- `invalidate_all`: `self._cache.scan_iter(...)`, `self._cache.unlink(...)`
- `list_cached`: `self._cache.scan_iter(...)`, `self._cache.get(...)`

- [ ] **Step 2: Update credential manager tests**

In `tests/unit_tests/test_credential_manager.py`, replace `mock_redis = AsyncMock()` patterns with `InProcessCredentialCache()` where possible (for simpler, more realistic tests), or keep `AsyncMock()` but update the constructor call from `redis_client=mock_redis` to `cache=mock_redis`.

The key change: every `CredentialManager(db_pool=..., redis_client=...)` becomes `CredentialManager(db_pool=..., cache=...)`.

- [ ] **Step 3: Run credential manager tests**

Run: `uv run pytest tests/unit_tests/test_credential_manager.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/luthien_proxy/credential_manager.py tests/unit_tests/test_credential_manager.py
git commit -m "refactor: CredentialManager uses CredentialCacheProtocol instead of Redis directly"
```

---

### Task 5: Wire EventEmitter to Use EventPublisherProtocol

**Files:**
- Modify: `src/luthien_proxy/observability/emitter.py`
- Modify: `src/luthien_proxy/observability/__init__.py`
- Modify: `tests/unit_tests/observability/test_emitter.py` (if it references `RedisEventPublisher` directly)

- [ ] **Step 1: Update EventEmitter constructor and all internal references**

In `emitter.py`:

1. Replace `from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher` with `from luthien_proxy.observability.event_publisher import EventPublisherProtocol`

2. Change constructor parameter and field name:
```python
def __init__(
    self,
    db_pool: "DatabasePool | None" = None,
    event_publisher: "EventPublisherProtocol | None" = None,
    stdout_enabled: bool = True,
):
    self._db_pool = db_pool
    self._event_publisher = event_publisher
    self._stdout_enabled = stdout_enabled
```

3. In `emit()` method (~line 172-173), rename the guard check:
   - `if self._redis_publisher:` → `if self._event_publisher:`
   - `tasks.append(self._write_redis(...))` → `tasks.append(self._write_events(...))`

4. Rename `_write_redis` to `_write_events` and update the cast:
```python
async def _write_events(self, transaction_id, event_type, data, timestamp):
    publisher = cast("EventPublisherProtocol", self._event_publisher)
    try:
        await publisher.publish_event(
            call_id=transaction_id,
            event_type=event_type,
            data=data,
        )
    except Exception as e:
        logger.warning(f"Failed to publish event: {e}", exc_info=True)
```

- [ ] **Step 2: Update `__init__.py` exports**

Add `EventPublisherProtocol` and `InProcessEventPublisher` to `observability/__init__.py` exports.

- [ ] **Step 3: Run emitter tests**

Run: `uv run pytest tests/unit_tests/observability/test_emitter.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/luthien_proxy/observability/emitter.py src/luthien_proxy/observability/__init__.py tests/unit_tests/observability/test_emitter.py
git commit -m "refactor: EventEmitter uses EventPublisherProtocol instead of RedisEventPublisher"
```

---

### Task 6: Wire Dependencies and UI Routes

**Files:**
- Modify: `src/luthien_proxy/dependencies.py`
- Modify: `src/luthien_proxy/ui/routes.py`

- [ ] **Step 1: Add event_publisher to Dependencies**

In `dependencies.py`:

1. Add import: `from luthien_proxy.observability.event_publisher import EventPublisherProtocol`

2. Add field to `Dependencies` dataclass:
```python
event_publisher: EventPublisherProtocol | None = field(default=None)
```

3. Keep `redis_client: Redis | None` for now. **Reason:** `PolicyManager` still uses the raw Redis client for distributed locking (it has its own `asyncio.Lock` fallback when Redis is None, so no protocol is needed there). Removing `redis_client` from Dependencies is a separate cleanup task once all Redis consumers have been migrated to protocols. This intentionally diverges from the spec's "replace redis_client" language — the spec didn't account for PolicyManager's direct Redis usage.

4. Add a dependency function:
```python
def get_event_publisher(request: Request) -> EventPublisherProtocol | None:
    return get_dependencies(request).event_publisher
```

- [ ] **Step 2: Update ui/routes.py to use event_publisher**

In `ui/routes.py`, change the activity_stream endpoint:

```python
from luthien_proxy.dependencies import get_event_publisher
from luthien_proxy.observability.event_publisher import EventPublisherProtocol

@router.get("/api/activity/stream")
async def activity_stream(
    _: str = Depends(verify_admin_token),
    publisher: EventPublisherProtocol | None = Depends(get_event_publisher),
):
    if not publisher:
        raise HTTPException(
            status_code=503,
            detail="Activity stream unavailable (no event publisher configured)",
        )
    return FastAPIStreamingResponse(
        publisher.stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

Remove the `redis.asyncio` import and `get_redis_client` dependency from this file.

- [ ] **Step 3: Run full unit tests to check for regressions**

Run: `uv run pytest tests/unit_tests/ -v --timeout=10`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/luthien_proxy/dependencies.py src/luthien_proxy/ui/routes.py
git commit -m "refactor: UI activity stream uses EventPublisherProtocol from dependencies"
```

---

### Task 7: Wire main.py to Create Correct Implementations

**Files:**
- Modify: `src/luthien_proxy/main.py`
- Modify: `src/luthien_proxy/gateway_routes.py`

- [ ] **Step 1: Update main.py create_app to build publishers and caches**

In `main.py` lifespan, replace the current Redis publisher creation:

```python
# Before:
_redis_publisher = RedisEventPublisher(redis_client) if redis_client else None
_emitter = EventEmitter(db_pool=db_pool, redis_publisher=_redis_publisher, stdout_enabled=True)

# After:
from luthien_proxy.observability.event_publisher import InProcessEventPublisher, EventPublisherProtocol
from luthien_proxy.utils.credential_cache import InProcessCredentialCache, RedisCredentialCache, CredentialCacheProtocol

_event_publisher: EventPublisherProtocol | None
if redis_client:
    _event_publisher = RedisEventPublisher(redis_client)
else:
    _event_publisher = InProcessEventPublisher()
    logger.info("Using in-process event publisher (no Redis)")

_emitter = EventEmitter(db_pool=db_pool, event_publisher=_event_publisher, stdout_enabled=True)
```

Similarly for credential cache:

```python
# Before:
_credential_manager = CredentialManager(db_pool=db_pool, redis_client=redis_client)

# After:
_credential_cache: CredentialCacheProtocol | None
if redis_client:
    _credential_cache = RedisCredentialCache(redis_client)
else:
    _credential_cache = InProcessCredentialCache()
    logger.info("Using in-process credential cache (no Redis)")

_credential_manager = CredentialManager(db_pool=db_pool, cache=_credential_cache)
```

Add `event_publisher=_event_publisher` to the Dependencies constructor call.

- [ ] **Step 2: Replace Redis-based last_credential_type with in-memory field**

This value is ephemeral metadata (not persisted across restarts), so an in-memory dict on Dependencies is the right home. This removes one Redis touchpoint entirely and works in both modes.

**In `dependencies.py`**, add field (requires `from typing import Any`):
```python
last_credential_info: dict[str, Any] = field(default_factory=dict)
```

**In `gateway_routes.py`**, replace the `_record_credential_type` inner function:
```python
async def _record_credential_type(cred_type: str) -> None:
    if auth_mode == AuthMode.PROXY_KEY:
        return
    deps = getattr(request.app.state, "dependencies", None)
    if deps:
        deps.last_credential_info = {"type": cred_type, "timestamp": time.time()}
```

Remove the `LAST_CRED_TYPE_KEY` and `LAST_CRED_TYPE_TTL` constants. Remove the Redis import/usage from this function.

**In `main.py` health endpoint**, replace the Redis read:
```python
# Before:
if deps and deps.redis_client:
    try:
        raw = await deps.redis_client.get(LAST_CRED_TYPE_KEY)
        if raw:
            data = json.loads(raw)
            last_credential_type = data.get("type")
            last_credential_at = data.get("timestamp")
    except Exception:
        logger.debug(...)

# After:
if deps and deps.last_credential_info:
    last_credential_type = deps.last_credential_info.get("type")
    last_credential_at = deps.last_credential_info.get("timestamp")
```

Remove the `LAST_CRED_TYPE_KEY` import from `main.py`.

- [ ] **Step 3: Run full unit tests**

Run: `uv run pytest tests/unit_tests/ -v --timeout=10`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/luthien_proxy/main.py src/luthien_proxy/gateway_routes.py src/luthien_proxy/dependencies.py
git commit -m "feat: wire in-process implementations for local mode without Redis"
```

---

### Task 8: Run Dev Checks and Fix Issues

**Files:** Any files that need formatting/lint fixes

- [ ] **Step 1: Run dev checks**

Run: `./scripts/dev_checks.sh`

- [ ] **Step 2: Fix any formatting, lint, or type errors**

Address issues reported by ruff and pyright.

- [ ] **Step 3: Run full unit tests with coverage**

Run: `uv run pytest tests/unit_tests/ -v --timeout=10`
Expected: All PASS

- [ ] **Step 4: Commit any fixes**

```bash
git add -u
git commit -m "chore: fix formatting and type errors from Redis replacement refactor"
```

---

### Task 9: Document Local Mode

**Files:**
- Modify: `CLAUDE.md` or `README.md` (whichever documents configuration)

- [ ] **Step 1: Add local mode documentation**

Document that local single-process mode works by leaving `REDIS_URL` unset:
- Activity monitor works via in-process pub/sub
- Credential validation cache works via in-process TTL dict
- Policy lock uses asyncio.Lock (already documented)
- Single-process assumption: no `--workers N` in local mode

- [ ] **Step 2: Commit**

```bash
git add -u
git commit -m "docs: document local mode without Redis"
```
