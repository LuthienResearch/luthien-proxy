# In-Process Redis Replacement for Local Mode

## Problem

Running luthien-proxy locally requires a Redis container for credential caching and real-time activity monitoring. For the local installed use case (SQLite DB, single process), Redis is unnecessary overhead. We want the full feature set — including the activity monitor — without any external dependency beyond the proxy process itself.

## Constraints

- Single-process assumption for local mode (document this)
- Independent of the SQLite backend toggle — `REDIS_URL` empty activates in-process replacements regardless of DB backend
- Forward-only event streaming (no replay buffer)
- All existing Redis-backed behavior must continue working unchanged when `REDIS_URL` is set

## Design: Protocol + In-Process Implementations

Same pattern as `db_sqlite.py` — define protocols for each Redis role, provide both Redis and in-process implementations.

### 1. Event Publisher (pub/sub for activity monitor)

**Protocol:** `EventPublisherProtocol`
- `publish_event(call_id, event_type, data)` — publish an event
- `stream_events(heartbeat_seconds, timeout_seconds)` → `AsyncGenerator[str, None]` — SSE stream

**Implementations:**
- `RedisEventPublisher` — existing, wraps Redis pub/sub. Absorbs `stream_activity_events()` as a method.
- `InProcessEventPublisher` — maintains a `set[asyncio.Queue]` of subscribers. `publish_event` pushes to all queues. `stream_events` registers a queue, yields SSE-formatted strings, removes queue on disconnect. Same heartbeat logic as Redis version.

**Wiring:**
- `EventEmitter` accepts `EventPublisherProtocol | None` instead of `RedisEventPublisher | None`
- `ui/routes.py` activity stream endpoint gets the publisher from dependencies instead of raw Redis client
- `Dependencies` dataclass: replace `redis_client: Redis | None` with `event_publisher: EventPublisherProtocol | None`

### 2. Credential Cache (TTL key-value store)

**Protocol:** `CredentialCacheProtocol`
- `get(key)` → `str | None`
- `setex(key, ttl, value)` — set with TTL
- `delete(key)` → `bool`
- `scan(prefix)` → `AsyncIterator[tuple[str, str]]` — iterate key-value pairs matching prefix
- `unlink(*keys)` → `int` — bulk delete
- `ttl(key)` → `int` — remaining TTL in seconds

**Implementations:**
- `RedisCredentialCache` — thin wrapper around existing `redis.asyncio.Redis` calls in `CredentialManager`
- `InProcessCredentialCache` — `dict[str, tuple[str, float]]` mapping key → (value, expiry_timestamp). TTL enforced on read (lazy expiry). `scan` skips expired entries.

**Wiring:**
- `CredentialManager.__init__` takes `CredentialCacheProtocol | None` instead of `Redis | None`
- All Redis-specific calls in `CredentialManager` route through the protocol

### 3. Last Credential Type (single ephemeral value)

This is just a key-value pair read by `/health` and written by the gateway route. Simplest approach: store it on a shared object rather than introducing a protocol for one key.

**Approach:** Add `last_credential_type: dict | None` as an in-memory field on `Dependencies` (or a small holder class). Gateway route writes to it; `/health` reads from it. When Redis is available, also write to Redis (existing behavior). When Redis is absent, the in-memory value is the only source.

### 4. Policy Lock

Already handled — `PolicyManager` falls back to `asyncio.Lock` when Redis is None. No changes needed.

## File Changes Summary

**New files:**
- `src/luthien_proxy/observability/event_publisher.py` — protocol + `InProcessEventPublisher`
- `src/luthien_proxy/utils/credential_cache.py` — protocol + `InProcessCredentialCache` + `RedisCredentialCache`

**Modified files:**
- `src/luthien_proxy/observability/redis_event_publisher.py` — implement protocol, absorb `stream_activity_events` as method
- `src/luthien_proxy/observability/emitter.py` — use protocol type instead of concrete Redis publisher
- `src/luthien_proxy/credential_manager.py` — use `CredentialCacheProtocol` instead of `Redis`
- `src/luthien_proxy/dependencies.py` — replace `redis_client` with `event_publisher`, add credential cache
- `src/luthien_proxy/ui/routes.py` — use event publisher from deps instead of raw Redis
- `src/luthien_proxy/main.py` — create appropriate implementations based on `REDIS_URL`, wire into deps
- `src/luthien_proxy/gateway_routes.py` — use in-memory fallback for last_credential_type

**Test files:**
- Unit tests for `InProcessEventPublisher`
- Unit tests for `InProcessCredentialCache`
- Update existing tests that mock Redis to use the protocol instead
