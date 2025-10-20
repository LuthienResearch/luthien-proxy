# Redis vs OpenTelemetry for Real-Time Monitoring

**Created:** 2025-10-20
**Status:** Analysis - Exploring improvements to observability architecture

---

## Current Architecture

### Two Parallel Systems

We currently maintain two separate observability channels:

1. **OpenTelemetry (OTel)** - Distributed tracing and historical analysis
   - Spans exported to Tempo via OTLP/gRPC
   - Viewed in Grafana (query by trace_id, call_id, etc.)
   - Batch processing with ~seconds delay
   - Rich structured data with full context

2. **Redis Pub/Sub** - Real-time activity stream
   - Lightweight JSON events published to `luthien:activity` channel
   - Consumed by `/v2/activity/stream` (SSE endpoint)
   - Rendered in `/v2/activity/monitor` (HTML UI)
   - Near-instant delivery (milliseconds)
   - Minimal data, fire-and-forget

### How They Work Together (Current)

```python
# In PolicyContext.emit() - src/luthien_proxy/v2/policies/context.py:48-104
def emit(self, event_type: str, summary: str, details: dict | None, severity: str):
    # 1. Add to OpenTelemetry span (always)
    self.span.add_event(event_type, attributes={...})

    # 2. Publish to Redis (if available, fire-and-forget)
    if self._event_publisher:
        asyncio.create_task(
            self._event_publisher.publish_event(call_id, event_type, data={...})
        )
```

The gateway layer also manually publishes events at key lifecycle points:
- `gateway.request_received` (main.py:204-212)
- `gateway.request_sent` (main.py:226-233)
- `gateway.response_received` (main.py:258-265)
- `gateway.response_sent` (main.py:277-285)

### The Problem

This dual-channel approach is "clunky" because:

1. **Duplication**: Same information flows through two different systems
2. **Manual coordination**: Gateway code explicitly publishes to both systems
3. **Inconsistency risk**: Easy to add OTel span event but forget Redis publish
4. **Maintenance burden**: Changes to events require updating multiple places
5. **Conceptual confusion**: Developers must understand both systems and when to use each

---

## Why We Need Both (Currently)

### OTel's Strengths
- **Rich context**: Full distributed tracing, parent/child spans, attributes
- **Query power**: Search by any attribute, view full trace trees
- **Industry standard**: Works with any OTel-compatible backend
- **Long retention**: Tempo stores 24h+ of traces (configurable)
- **Correlation**: Links logs ↔ traces ↔ metrics

### OTel's Weaknesses for Real-Time
- **Batching delay**: BatchSpanProcessor batches spans before export (~seconds)
- **Backend delay**: Tempo ingestion and indexing adds latency
- **Query-based**: Must poll/query Grafana, not push-based
- **Heavy**: Full span data is overkill for "what's happening right now?"

### Redis's Strengths
- **Instant delivery**: Pub/sub is push-based, ~millisecond latency
- **Simple**: Lightweight JSON events, no complex schema
- **Live streams**: SSE endpoint provides real-time updates to browser
- **Low overhead**: Fire-and-forget, doesn't block request processing

### Redis's Weaknesses
- **No persistence**: Events disappear after delivery (unless explicitly logged)
- **No structure**: Just JSON blobs, no parent/child relationships
- **No querying**: Can't search past events or correlate across requests
- **Single channel**: All events mixed together (could filter client-side)

---

## Alternative Approaches Considered

### Option 1: OTel SpanExporter Hook (Custom Exporter)

**Idea**: Create a custom OTel `SpanExporter` that publishes to Redis in addition to Tempo.

```python
class RedisSpanExporter(SpanExporter):
    """Export OTel spans to Redis in real-time."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            # Extract key info from span
            event = {
                "call_id": span.attributes.get("luthien.call_id"),
                "event_type": span.name,
                "timestamp": span.start_time,
                "data": {k: v for k, v in span.attributes.items()}
            }
            # Publish to Redis
            await redis.publish("luthien:activity", json.dumps(event))

        return SpanExportResult.SUCCESS
```

**Pros:**
- Single source of truth (OTel spans)
- Automatic - no manual Redis publishing needed
- Spans and Redis events guaranteed consistent

**Cons:**
- Still has batching delay (SpanProcessor batches before calling exporter)
- Complex to make async (exporters are sync API, we need async Redis)
- Events appear at span *end*, not as they happen during execution
- Harder to control what goes to Redis (all spans vs selective events)

### Option 2: OTel Logs Signal + Log Exporter

**Idea**: Use OTel's Logs signal (not just Traces) and export logs to Redis.

OTel has three signals: Traces, Metrics, Logs. We currently only use Traces. The Logs signal is designed for structured event logging.

```python
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

# Custom log exporter that publishes to Redis
class RedisLogExporter(LogExporter):
    def export(self, batch: Sequence[LogRecord]) -> LogExportResult:
        for log in batch:
            # Publish to Redis
            ...
```

**Pros:**
- Uses official OTel abstraction for events
- Can still correlate logs ↔ traces via trace_id
- More semantic (these are "events", not "traces")

**Cons:**
- Adds complexity (third signal to manage)
- Still has batching/export delay
- OTel Logs signal less mature than Traces
- Would need to refactor PolicyContext.emit() significantly

### Option 3: Real-Time Span Events via SpanProcessor

**Idea**: Create a custom `SpanProcessor` that watches for `span.add_event()` calls and immediately publishes to Redis.

OpenTelemetry has two hooks:
- `SpanProcessor.on_start(span)` - when span starts
- `SpanProcessor.on_end(span)` - when span ends

Unfortunately, there's no `on_event()` hook for when events are added to a span. We'd need to wrap the Span class itself.

**Pros:**
- Could achieve true real-time publishing
- Keeps PolicyContext.emit() unchanged

**Cons:**
- Not supported by OTel API (would require monkey-patching)
- Fragile and non-standard
- Complex implementation

### Option 4: Keep Dual System but Automate Coordination

**Idea**: Keep both systems but make the coordination automatic and foolproof.

Current `PolicyContext.emit()` already does this! The issue is the *gateway* layer manually publishes events outside PolicyContext.

**Improvement**: Move all event publishing through a centralized helper:

```python
# New: src/luthien_proxy/v2/observability/events.py
async def emit_gateway_event(
    span: Span,
    call_id: str,
    event_type: str,
    data: dict,
    event_publisher: SimpleEventPublisher | None,
):
    """Emit an event to both OTel and Redis.

    Single function for all event emission, ensures consistency.
    """
    # Add to OTel span
    span.add_event(event_type, attributes={...})

    # Publish to Redis
    if event_publisher:
        asyncio.create_task(
            event_publisher.publish_event(call_id, event_type, data)
        )
```

Then refactor gateway code to use this instead of directly calling both systems.

**Pros:**
- Simple, incremental improvement
- Keeps both systems (best of both worlds)
- Makes dual-publishing explicit and consistent
- Easy to understand and maintain

**Cons:**
- Still conceptually two systems
- Doesn't eliminate Redis infrastructure dependency

### Option 5: Replace Redis with Grafana Live/Alerting

**Idea**: Use Grafana's built-in real-time features instead of custom Redis/SSE.

Grafana has:
- **Grafana Live**: WebSocket-based real-time streaming
- **Alerting**: Can push notifications on query results
- **Streaming dashboards**: Auto-refresh panels

**Pros:**
- No custom UI needed
- Uses existing Grafana infrastructure
- Industry-standard approach

**Cons:**
- Less control over UX
- Requires Grafana Enterprise for some features
- Still query-based (polls Tempo), not true push
- Harder to customize for policy-specific events

---

## Recommendation

**Short term (now)**: **Option 4** - Keep dual system but improve coordination

1. Create centralized `emit_gateway_event()` helper
2. Refactor gateway layer to use it consistently
3. Add tests that verify OTel + Redis publishing happen together
4. Document the two-channel architecture clearly in observability-guide.md

**Why:**
- Pragmatic: Works with existing infrastructure
- Low risk: Small refactor, no architectural changes
- Preserves strengths: OTel for analysis, Redis for real-time
- Easy to understand: One function = one event, goes to both places

**Medium term (next sprint)**: Explore OTel → Redis bridge via custom exporter

1. Prototype custom `SpanExporter` that also publishes to Redis
2. Benchmark latency vs current approach
3. Evaluate if batching delay is acceptable for our use case
4. If successful, this could replace manual Redis publishing entirely

**Long term (future)**: Consider OpenTelemetry Collector

The [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/) is a vendor-agnostic proxy that can:
- Receive traces/logs/metrics from your app
- Process, filter, transform them
- Export to multiple backends (Tempo, Redis, custom endpoints)

```yaml
# otel-collector-config.yaml
receivers:
  otlp:
    protocols:
      grpc:

processors:
  batch:
    timeout: 100ms  # Low latency for real-time

exporters:
  otlp/tempo:
    endpoint: tempo:4317

  redis:  # Custom exporter (would need to implement)
    endpoint: redis:6379
    channel: luthien:activity

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/tempo, redis]
```

**Pros:**
- Industry standard architecture
- Centralized configuration
- Can add more exporters easily (e.g., cloud vendors)
- App code doesn't care about backends

**Cons:**
- Another component to deploy/monitor
- Custom Redis exporter would need development
- Adds latency (app → collector → backends)
- May be overkill for our current scale

---

## Questions for Discussion

1. **What's the acceptable latency for real-time monitoring?**
   - If 1-2 second delay is fine, OTel exporter approach works
   - If we need <100ms, must keep Redis pub/sub

2. **Who uses the real-time monitor?**
   - Developers debugging locally? (can tolerate latency)
   - Production monitoring? (needs instant alerting)
   - Demos/showcases? (needs visual responsiveness)

3. **How important is event persistence?**
   - Currently Redis events disappear after delivery
   - OTel spans persist in Tempo for 24h
   - Do we need a "replay recent activity" feature?

4. **Could Grafana dashboards replace the custom HTML monitor?**
   - Would save us from maintaining custom UI
   - But less flexibility for policy-specific visualizations

---

## Implementation Notes

If we go with **Option 4** (centralized helper), here's the implementation plan:

### Step 1: Create centralized event emitter

```python
# src/luthien_proxy/v2/observability/events.py

from opentelemetry.trace import Span
from typing import Any, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

async def emit_event(
    span: Span,
    call_id: str,
    event_type: str,
    summary: str,
    data: Optional[dict[str, Any]] = None,
    severity: str = "info",
    event_publisher: Optional[SimpleEventPublisher] = None,
) -> None:
    """Emit an event to both OpenTelemetry and Redis.

    This is the single source of truth for event emission.
    Ensures that OTel spans and Redis pub/sub stay in sync.

    Args:
        span: OpenTelemetry span to add event to
        call_id: Request identifier
        event_type: Event type (e.g., "gateway.request_received")
        summary: Human-readable summary
        data: Optional structured data
        severity: Severity level (debug/info/warning/error)
        event_publisher: Optional Redis publisher for real-time UI
    """
    # 1. Add to OpenTelemetry span
    attributes = {
        "event.type": event_type,
        "event.summary": summary,
        "event.severity": severity,
    }

    if data:
        for key, value in data.items():
            if isinstance(value, (str, int, float, bool)):
                attributes[f"event.{key}"] = value
            else:
                attributes[f"event.{key}"] = str(value)

    span.add_event(event_type, attributes=attributes)
    logger.debug(f"Added OTel event: {event_type} for call {call_id}")

    # 2. Publish to Redis (fire-and-forget)
    if event_publisher:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(
                    event_publisher.publish_event(
                        call_id=call_id,
                        event_type=event_type,
                        data={"summary": summary, "severity": severity, **(data or {})},
                    )
                )
        except RuntimeError:
            # No event loop - skip Redis publish
            logger.debug(f"Skipped Redis publish for {event_type} (no event loop)")
```

### Step 2: Update PolicyContext to use it

```python
# src/luthien_proxy/v2/policies/context.py

from luthien_proxy.v2.observability.events import emit_event

class PolicyContext:
    def emit(self, event_type: str, summary: str, details: dict | None, severity: str):
        """Emit a policy event (delegates to centralized emitter)."""
        asyncio.create_task(
            emit_event(
                span=self.span,
                call_id=self.call_id,
                event_type=event_type,
                summary=summary,
                data=details,
                severity=severity,
                event_publisher=self._event_publisher,
            )
        )
```

### Step 3: Refactor gateway code

```python
# src/luthien_proxy/v2/main.py

from luthien_proxy.v2.observability.events import emit_event

# Replace all manual publishing with:
await emit_event(
    span=span,
    call_id=call_id,
    event_type="gateway.request_received",
    summary=f"Request received: {data.get('model')} (stream={data.get('stream')})",
    data={
        "endpoint": "/v1/chat/completions",
        "model": data.get("model", "unknown"),
        "stream": data.get("stream", False),
    },
    event_publisher=event_publisher,
)
```

### Step 4: Add tests

```python
# tests/unit_tests/v2/test_observability_events.py

async def test_emit_event_adds_to_span_and_redis():
    """Verify emit_event() publishes to both OTel and Redis."""
    mock_span = Mock()
    mock_publisher = Mock()

    await emit_event(
        span=mock_span,
        call_id="test-123",
        event_type="test.event",
        summary="Test event",
        data={"foo": "bar"},
        event_publisher=mock_publisher,
    )

    # Verify OTel span event
    mock_span.add_event.assert_called_once()

    # Verify Redis publish (eventually)
    await asyncio.sleep(0.1)  # Let task complete
    mock_publisher.publish_event.assert_called_once()
```

---

## Conclusion

The current dual-channel architecture is pragmatic but could be more elegant. The immediate path forward is to centralize event emission to prevent drift between OTel and Redis. Longer term, we should explore using OTel Collector or custom exporters to eliminate manual coordination entirely.

The key insight is that **Redis serves a different use case than OTel**: real-time push notifications vs historical analysis. Any solution must preserve both capabilities while reducing maintenance burden.
