# Coverage Documentation: v2/observability/redis_event_publisher.py

**Module:** `src/luthien_proxy/v2/observability/redis_event_publisher.py`
**Coverage:** 30%

## Coverage Gaps (70%)

- **Lines 68-83:** `publish_event()` - async Redis publishing
- **Lines 96-97:** `create_event_publisher()` - async Redis client creation
- **Lines 115-154:** `stream_activity_events()` - async SSE streaming with Redis pub/sub

## Why Limited Unit Testing?

This module has limited unit test coverage by design.

### Rationale

- `RedisEventPublisher.publish_event()` performs async Redis operations
- `stream_activity_events()` is a complex async generator with Redis pub/sub
- Testing these properly requires either:
  1. Heavy mocking of Redis client (diverges from real behavior)
  2. Integration tests with actual Redis instance (better approach)

### Current Unit Test Coverage

- Basic object initialization
- Error handling paths

### Integration Test Coverage

Integration tests cover:

- Actual event publishing to Redis
- SSE streaming of events to clients
- Heartbeat mechanism
- Timeout handling
- Error scenarios

## Suggested Refactoring (Isomorphic)

### Extract Event Building Logic

**Current code** (lines 68-77):

```python
event: dict[str, Any] = {
    "call_id": call_id,
    "event_type": event_type,
    "timestamp": datetime.now(UTC).isoformat(),
}
if data:
    event["data"] = data
```

**Refactored to pure function:**

```python
def build_activity_event(
    call_id: str,
    event_type: str,
    data: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build activity event dict for Redis publication.

    Args:
        call_id: Unique request identifier
        event_type: Event type (e.g., "policy.content_filtered")
        data: Optional event-specific data
        timestamp: Optional timestamp (defaults to now)

    Returns:
        Event dict ready for JSON serialization
    """
    event: dict[str, Any] = {
        "call_id": call_id,
        "event_type": event_type,
        "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
    }
    if data:
        event["data"] = data
    return event
```

**Updated publish_event:**

```python
async def publish_event(self, call_id: str, event_type: str, data: dict[str, Any] | None = None) -> None:
    event = build_activity_event(call_id, event_type, data)
    try:
        await self.redis.publish(self.channel, json.dumps(event))
        logger.debug(f"Published event: {event_type} for call {call_id}")
    except Exception as e:
        logger.error(f"Failed to publish event to Redis: {e}")
```

### Benefits

- `build_activity_event()` is a pure function, easily unit testable
- Can test timestamp formatting, data inclusion, edge cases without Redis
- Original behavior preserved exactly (isomorphic refactor)
- `publish_event()` becomes simpler, focused on I/O

Similar refactoring opportunity exists for SSE formatting in `stream_activity_events()`.

## Adding Unit Tests

If `build_activity_event()` is extracted, add tests to:

- `tests/unit_tests/v2/observability/test_redis_event_publisher.py`

Example tests:

```python
def test_build_activity_event_minimal():
    event = build_activity_event("call-123", "policy.test")
    assert event["call_id"] == "call-123"
    assert event["event_type"] == "policy.test"
    assert "timestamp" in event
    assert "data" not in event

def test_build_activity_event_with_data():
    event = build_activity_event("call-123", "policy.test", data={"key": "value"})
    assert event["data"] == {"key": "value"}
```

Follow guidelines in [tests/unit_tests/CLAUDE.md](../../CLAUDE.md)
