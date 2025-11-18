# Observability Sink-Based Architecture Refactor

**Date Started**: 2025-11-18
**Status**: Partially Complete (architecture implemented, tests need updating)
**Related Decision**: [dev/context/decisions.md](context/decisions.md#observability-strategy-custom-observabilitycontext-2025-11-18)

## Overview

Refactored the observability system from a monolithic "send to all destinations" approach to a flexible sink-based architecture with configurable routing. This enables better testing (inject mock sinks), clearer separation of concerns, and environment-specific configurations.

## Motivation

### Problems with Old Architecture

1. **Testing Difficulty**: No way to inject mocks - tests would hit real DB/Redis or require complex mocking
2. **Hardcoded Routing**: Every event went to all 4 destinations (Loki, DB, Redis, OTel) - no flexibility
3. **Implicit Dependencies**: `DefaultObservabilityContext` constructor took `db_pool` and `event_publisher` but relationship to sinks was opaque
4. **Wrapper Methods**: OTel span wrapped with `add_span_attribute()` instead of exposed directly

### Design Goals

1. **Testing Isolation**: Inject mock sinks to avoid touching external services
2. **Explicit Control**: Clear configuration of which record types go to which sinks
3. **Type Safety**: Literal types for sink names, class types for routing keys
4. **Flexibility**: Different routing per environment (dev uses fewer sinks than prod)
5. **Direct OTel Access**: Expose `obs_ctx.span` directly without wrappers

## What Was Completed

### 1. Sink Infrastructure ([src/luthien_proxy/observability/sinks.py](../src/luthien_proxy/observability/sinks.py))

Created base class and four concrete implementations:

```python
class LuthienRecordSink(ABC):
    """Base class for observability sinks."""

    @abstractmethod
    async def write(self, record: LuthienRecord) -> None:
        """Write record to this sink's destination."""
        pass
```

**Implementations:**

- **LokiSink**: Writes to stdout via `write_json_to_stdout()` (Promtail collects)
- **DatabaseSink**: Persists to PostgreSQL (placeholder implementation - TODO)
- **RedisSink**: Publishes to Redis pub/sub (placeholder implementation - TODO)
- **OTelSink**: Adds attributes to OpenTelemetry span

Each sink encapsulates its dependencies internally (db_pool, event_publisher, span).

### 2. Configuration Types ([src/luthien_proxy/observability/context.py](../src/luthien_proxy/observability/context.py))

```python
# Type-safe sink names
SinkName = Literal["loki", "db", "redis", "otel"]

class ObservabilityConfig(TypedDict, total=False):
    """Configuration for observability sink routing."""
    loki_sink: LuthienRecordSink | None
    db_sink: LuthienRecordSink | None
    redis_sink: LuthienRecordSink | None
    otel_sink: LuthienRecordSink | None
    routing: dict[type[LuthienRecord], list[SinkName]]
    default_sinks: list[SinkName]
```

**Key Design**: Use record classes (not strings) as dict keys for type safety.

### 3. Refactored DefaultObservabilityContext

**Old signature:**
```python
def __init__(self, transaction_id, span, db_pool, event_publisher):
    # Hardcoded to emit to all destinations
```

**New signature:**
```python
def __init__(self, transaction_id, span, config: ObservabilityConfig | None = None):
    # Build sink registry with dependency injection
    self._sinks: dict[SinkName, LuthienRecordSink] = {
        "loki": config.get("loki_sink") or LokiSink(),
        "db": config.get("db_sink") or DatabaseSink(None),
        "redis": config.get("redis_sink") or RedisSink(None),
        "otel": config.get("otel_sink") or OTelSink(span),
    }

    # Configurable routing
    self._routing: dict[type[LuthienRecord], list[SinkName]] = config.get("routing", {})
    self._default_sink_names: list[SinkName] = config.get("default_sinks", ["loki"])
```

**Routing logic:**
```python
def record(self, record: LuthienRecord) -> None:
    """Route record to configured sinks (non-blocking)."""
    sink_names = self._routing.get(type(record), self._default_sink_names)

    async def _write_to_sinks():
        for name in sink_names:
            await self._sinks[name].write(record)

    asyncio.create_task(_write_to_sinks())
```

### 4. Exposed Span Directly

**Old API (wrapped):**
```python
obs_ctx.add_span_attribute("key", "value")
obs_ctx.add_span_event("event", {"data": "value"})
```

**New API (direct access):**
```python
obs_ctx.span.set_attribute("key", "value")
obs_ctx.span.add_event("event", {"data": "value"})
```

### 5. Updated Gateway Routes ([src/luthien_proxy/gateway_routes.py](../src/luthien_proxy/gateway_routes.py))

```python
# Create observability context with sink configuration
config: ObservabilityConfig = {
    "db_sink": DatabaseSink(db_pool) if db_pool else None,
    "redis_sink": RedisSink(event_publisher) if event_publisher else None,
    "otel_sink": OTelSink(span),
    "routing": {
        PipelineRecord: ["loki", "db", "redis", "otel"],
    },
    "default_sinks": ["loki"],
}
obs_ctx = DefaultObservabilityContext(
    transaction_id=call_id,
    span=span,
    config=config,
)
```

### 6. Backward Compatibility

Added deprecated methods to `ObservabilityContext` base class for gradual migration:

```python
# TODO: Remove these compatibility methods once all code migrates to LuthienRecords
def emit_event_nonblocking(self, event_type: str, data: dict, level: str = "INFO") -> None:
    """Deprecated: Use record() with LuthienRecord instead."""
    logger.warning(f"emit_event_nonblocking is deprecated...")

async def emit_event(self, event_type: str, data: dict, level: str = "INFO") -> None:
    """Deprecated: Use record() with LuthienRecord instead."""
    logger.warning(f"emit_event is deprecated...")

def add_span_attribute(self, key: str, value: str | int | float | bool) -> None:
    """Deprecated: Use obs_ctx.span.set_attribute() instead."""
    logger.warning(f"add_span_attribute is deprecated...")

def record_metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
    """Deprecated: Use OpenTelemetry metrics directly."""
    logger.warning(f"record_metric is deprecated...")
```

These currently just log warnings but don't actually perform the operations. This is intentional - forces callers to migrate.

## What Remains

### 1. Fix Tests (HIGH PRIORITY)

**Current Status**: ~20 unit tests failing because they use the old API

**Failing test categories:**

1. **tests/unit_tests/observability/test_context.py**
   - Tests expect deprecated methods to actually work (call span.set_attribute, etc.)
   - Need to either:
     - Update tests to use new API (`obs_ctx.span.set_attribute()` instead of `obs_ctx.add_span_attribute()`)
     - Make deprecated methods actually delegate (but then add deprecation warnings)

2. **tests/unit_tests/test_main.py**
   - App initialization tests likely fail due to signature changes
   - Need to update how DefaultObservabilityContext is instantiated

3. **Policy and streaming tests**
   - Various tests may be creating DefaultObservabilityContext with old signature
   - Search for `DefaultObservabilityContext(` calls in test files

**Recommended approach:**

1. Make deprecated compatibility methods actually work (delegate to real implementation) to unblock tests
2. Update tests gradually to use new API
3. Remove deprecated methods once all tests migrated

### 2. Implement Database and Redis Sinks (MEDIUM PRIORITY)

**Current status**: Placeholder implementations that just log

**DatabaseSink TODO:**
```python
async def write(self, record: LuthienRecord) -> None:
    """Write record to PostgreSQL."""
    if not self._db_pool:
        return

    # Convert LuthienRecord to conversation_events format
    # Call existing storage functions or write directly to DB
    # Reference: src/luthien_proxy/storage/events.py::emit_custom_event()
```

**RedisSink TODO:**
```python
async def write(self, record: LuthienRecord) -> None:
    """Write record to Redis pub/sub."""
    if not self._event_publisher:
        return

    # Publish record to Redis channel
    # Reference: existing event_publisher.publish_event() calls
```

### 3. Migrate Old Code to LuthienRecords (MEDIUM PRIORITY)

**Files still using deprecated `emit_event()` API:**

- `src/luthien_proxy/observability/transaction.py` (~5 callsites)
- `src/luthien_proxy/observability/transaction_recorder.py` (~10 callsites)

**Migration strategy:**

1. Define new LuthienRecord types for these events (e.g., `RequestIncomingRecord`, `BackendRequestRecord`)
2. Replace `emit_event()` calls with `record(new_record_type())`
3. Update routing configuration to include new record types

**Example migration:**
```python
# Old
await self.obs_ctx.emit_event(
    event_type="luthien.request.incoming",
    data={"endpoint": endpoint, "body": body},
)

# New - define record type
class RequestIncomingRecord(LuthienRecord):
    record_type = "request_incoming"

    def __init__(self, transaction_id: str, endpoint: str, body: dict):
        super().__init__(transaction_id)
        self.endpoint = endpoint
        self.body = json.dumps(body)

# New - use record
self.obs_ctx.record(
    RequestIncomingRecord(
        transaction_id=self._transaction_id,
        endpoint=endpoint,
        body=body,
    )
)
```

### 4. Add Sink Unit Tests (LOW PRIORITY)

**Test coverage needed:**

1. **LokiSink tests**:
   - Verify `write_json_to_stdout()` called with correct data
   - Verify record fields serialized correctly

2. **DatabaseSink tests**:
   - Mock db_pool, verify correct SQL/function calls
   - Test with None db_pool (should not crash)

3. **RedisSink tests**:
   - Mock event_publisher, verify publish_event called
   - Test with None event_publisher (should not crash)

4. **OTelSink tests**:
   - Verify span.set_attribute() called with record data
   - Test with non-recording span (should not crash)

5. **Routing tests**:
   - Verify correct sinks receive correct record types
   - Verify default_sinks used for unknown record types
   - Verify error handling (sink failure doesn't crash)

### 5. Remove Deprecated Methods (LOW PRIORITY)

Once all code migrated:

1. Remove `emit_event()`, `emit_event_nonblocking()`, `add_span_attribute()`, `record_metric()` from `ObservabilityContext`
2. Update `NoOpObservabilityContext` to remove overrides
3. Clean up any remaining compatibility shims

## Important Context for Takeover

### Key Files

- **[src/luthien_proxy/observability/sinks.py](../src/luthien_proxy/observability/sinks.py)**: Sink base class and implementations
- **[src/luthien_proxy/observability/context.py](../src/luthien_proxy/observability/context.py)**: ObservabilityContext with new config-based initialization
- **[src/luthien_proxy/gateway_routes.py](../src/luthien_proxy/gateway_routes.py)**: Example of new usage pattern
- **[tests/unit_tests/observability/test_context.py](../tests/unit_tests/observability/test_context.py)**: Tests that need updating

### Design Principles

1. **Sinks encapsulate dependencies**: DatabaseSink owns db_pool, RedisSink owns event_publisher
2. **Records format themselves**: Each LuthienRecord subclass knows its own schema
3. **Routing by type, not string**: Use `type(record)` not `record.record_type` for routing keys
4. **Non-blocking by default**: All sink writes are fire-and-forget via `asyncio.create_task()`
5. **Fail gracefully**: Sink errors are logged but don't crash the request

### Type Safety Features

- `SinkName = Literal["loki", "db", "redis", "otel"]` - catches typos at type-check time
- `routing: dict[type[LuthienRecord], list[SinkName]]` - ensures only valid sink names used
- Record classes as dict keys - IDE autocomplete shows available record types

### Testing Strategy

**For isolated unit tests:**
```python
# Create mock sinks
mock_loki = Mock(spec=LokiSink)
mock_loki.write = AsyncMock()

config: ObservabilityConfig = {
    "loki_sink": mock_loki,
    "db_sink": None,  # Disable DB for this test
    "redis_sink": None,  # Disable Redis for this test
    "routing": {
        PipelineRecord: ["loki"],
    },
}

obs_ctx = DefaultObservabilityContext(
    transaction_id="test-123",
    span=mock_span,
    config=config,
)

# Verify only mock_loki.write() was called
```

### Common Gotchas

1. **Don't forget to create sinks with dependencies**: `DatabaseSink(db_pool)` not `DatabaseSink()`
2. **Routing uses class types not strings**: `{PipelineRecord: [...]}` not `{"pipeline": [...]}`
3. **Deprecated methods don't actually work**: They log warnings but don't perform operations
4. **Sink writes are non-blocking**: Use `await` in tests to ensure completion
5. **Default sinks are just Loki**: If you don't specify routing, only Loki receives records

### Related Documentation

- [dev/context/decisions.md](context/decisions.md#observability-strategy-custom-observabilitycontext-2025-11-18) - Architecture decision rationale
- [dev/context/observability_records.md](context/observability_records.md) - LuthienRecord guide
- [dev/TODO.md](TODO.md) - Full TODO list including test migration tasks

## Commits

- `f64cedc` - Simplify telemetry, clarify observability strategy
- `4dd4c36` - Implement sink-based architecture with configurable routing

## Next Steps (Recommended Priority Order)

1. **Make deprecated methods work** - Quick fix to unblock tests (add delegation logic)
2. **Fix failing tests** - Get test suite green again
3. **Implement DatabaseSink** - Complete DB persistence
4. **Implement RedisSink** - Complete Redis pub/sub
5. **Migrate transaction.py** - Remove deprecated API usage
6. **Migrate transaction_recorder.py** - Remove deprecated API usage
7. **Add sink unit tests** - Ensure sink implementations work correctly
8. **Remove deprecated methods** - Clean up after migration complete

## Questions?

See [dev/context/decisions.md](context/decisions.md) for architectural rationale or ask the team.
