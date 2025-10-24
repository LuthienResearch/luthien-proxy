# Observability Guide

**Last Updated:** 2025-10-18
**Status:** Active - OpenTelemetry migration complete

> **NOTE**: For V2 observability implementation, see [`dev/observability-v2.md`](../observability-v2.md)

---

## Overview

Luthien Proxy V2 uses **OpenTelemetry** for distributed tracing and **Redis pub/sub** for real-time UI updates. This dual approach provides:

- **Distributed tracing** via OpenTelemetry → Tempo (for debugging and analysis)
- **Real-time monitoring** via Redis → WebSocket (for live activity feed)
- **Correlation** between traces and logs in Grafana

---

## Architecture

```
Request → Gateway (main.py)
          ↓ (creates span)
          ControlPlaneLocal
          ↓ (creates span)
          StreamingOrchestrator (optional span)
          ↓ (creates span)
          Policy (emits span events)
          ↓
          OpenTelemetry SDK → Tempo (gRPC)
          SimpleEventPublisher → Redis → WebSocket → UI
```

### Key Components

1. **OpenTelemetry SDK** (`v2/telemetry.py`)
   - Configures OTLP exporter to Tempo
   - Instruments FastAPI and Redis automatically
   - Adds trace context to logs (trace_id, span_id)
   - Exports `tracer` for manual instrumentation

2. **SimpleEventPublisher** (`v2/observability/bridge.py`)
   - Publishes lightweight events to Redis channel `luthien:activity`
   - Fire-and-forget (non-blocking)
   - Used for real-time UI at `/v2/activity/monitor`

3. **PolicyContext** (`v2/policies/context.py`)
   - Carries OTel span + optional event publisher
   - `emit()` method adds span events and optionally publishes to Redis

---

## Span Hierarchy

Every request creates a hierarchy of spans:

```
gateway.chat_completions (main.py)
├── control_plane.process_request (local.py)
│   └── policy events (via PolicyContext.emit)
└── control_plane.process_streaming_response (local.py)
    ├── orchestrator.start (streaming.py)
    ├── orchestrator.complete (streaming.py)
    └── policy events (via PolicyContext.emit)
```

### Span Attributes

All spans include these attributes:

- `luthien.call_id` - Unique request identifier
- `luthien.endpoint` - API endpoint (e.g., "/v1/chat/completions")
- `luthien.model` - LLM model name
- `luthien.stream` - Boolean, is streaming enabled
- `luthien.policy.name` - Policy class name
- `luthien.stream.chunk_count` - Number of chunks processed (streaming only)

See [otel-conventions.md](./otel-conventions.md) for full attribute list.

---

## How to Use OpenTelemetry

Note: The V2 architecture runs as a single integrated service in `main.py`. The sections below describe where to add instrumentation in different layers of the codebase, not separate services.

### In API Gateway Layer (main.py)

The gateway layer handles HTTP requests and coordinates the overall request flow.

```python
from luthien_proxy.v2.telemetry import tracer

with tracer.start_as_current_span("gateway.chat_completions") as span:
    span.set_attribute("luthien.call_id", call_id)
    span.set_attribute("luthien.model", model)

    # ... your code ...

    span.add_event("gateway.request_received")
```

### In Control Logic Layer (control/local.py)

The control logic layer executes policy methods and orchestrates streaming.

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("control_plane.process_request") as span:
    span.set_attribute("luthien.call_id", call_id)

    # Create PolicyContext with span
    ctx = PolicyContext(call_id=call_id, span=span, event_publisher=self._event_publisher)

    # Pass context to policy
    result = await policy.process_request(request, ctx)
```

### In Policy Implementations (policies/*.py)

Policy implementations contain custom control logic for specific use cases.

```python
async def process_request(self, request: Request, ctx: PolicyContext) -> Request:
    # Emit events that add to the current span
    ctx.emit(
        event_type="policy.content_filtered",
        summary="Blocked harmful content",
        details={"reason": "sql_injection", "pattern": "DROP TABLE"},
        severity="warning"
    )

    return modified_request
```

---

## Real-Time UI Events

The `SimpleEventPublisher` sends lightweight events to Redis for the real-time activity monitor.

### Event Format

```json
{
  "call_id": "abc123",
  "event_type": "policy.content_filtered",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "summary": "Blocked harmful content",
    "severity": "warning",
    "reason": "sql_injection"
  }
}
```

### Subscribing to Events

```python
import redis.asyncio as redis

redis_client = await redis.from_url("redis://localhost:6379")
pubsub = redis_client.pubsub()

await pubsub.subscribe("luthien:activity")

async for message in pubsub.listen():
    if message["type"] == "message":
        event = json.loads(message["data"])
        print(f"Event: {event['event_type']} for {event['call_id']}")
```

The `/v2/activity/monitor` endpoint streams these events as SSE.

---

## Environment Variables

Configure OpenTelemetry in `.env`:

```bash
# Enable/disable OpenTelemetry
OTEL_ENABLED=true

# Tempo endpoint (Docker service name or localhost)
OTEL_ENDPOINT=http://tempo:4317  # Docker
# OTEL_ENDPOINT=http://localhost:4317  # Local

# Service metadata
SERVICE_NAME=luthien-proxy-v2
SERVICE_VERSION=2.0.0
ENVIRONMENT=development
```

---

## Starting the Observability Stack

### 1. Start Tempo, Loki, Grafana

```bash
./scripts/observability.sh up -d
```

This starts:
- **Tempo** on port 4317 (gRPC) and 3200 (HTTP)
- **Loki** on port 3100
- **Grafana** on port 3000

### 2. Verify Services

```bash
./scripts/observability.sh status
```

All three services should be "Up".

### 3. Access Grafana

Open http://localhost:3000

- Username: `admin`
- Password: `admin`

Datasources are auto-configured:
- **Tempo** for traces
- **Loki** for logs

---

## Viewing Traces in Grafana

### Option 1: Explore Tab

1. Go to Grafana → Explore
2. Select **Tempo** datasource
3. Search by:
   - **Trace ID** (from logs)
   - **Service name**: `luthien-proxy-v2`
   - **Tags**: `luthien.call_id=<call_id>`

### Option 2: Logs → Traces Correlation

1. Go to Grafana → Explore
2. Select **Loki** datasource
3. Query: `{service="luthien-proxy-v2"} |= "call_id"`
4. Click on any log line → "Tempo" button → jumps to trace

---

## Troubleshooting

### No Traces in Tempo

**Check 1:** Is OTEL_ENABLED=true?

```bash
grep OTEL_ENABLED .env
```

**Check 2:** Is Tempo running?

```bash
docker compose ps tempo
```

**Check 3:** Are traces being exported?

```bash
docker compose logs control-plane | grep -i otel
# Should see: "OpenTelemetry initialized"
```

**Check 4:** Can the app reach Tempo?

```bash
docker compose exec control-plane curl http://tempo:4317
# Should connect (even if it returns an error, connection works)
```

### Real-Time UI Not Updating

**Check 1:** Is Redis running?

```bash
docker compose ps redis
```

**Check 2:** Is SimpleEventPublisher initialized?

```bash
docker compose logs control-plane | grep -i "event publisher"
# Should see: "Event publisher initialized for real-time UI"
```

**Check 3:** Are events being published?

```bash
docker compose exec redis redis-cli
> SUBSCRIBE luthien:activity
# Make a test request, you should see events
```

### Logs Missing trace_id

**Check:** Is telemetry initialized before logging?

The `setup_telemetry(app)` call must happen early in the lifespan, before any requests are logged.

---

## Best Practices

### 1. Span Naming Convention

Use `module.operation` format:

- ✅ `gateway.chat_completions`
- ✅ `control_plane.process_request`
- ✅ `orchestrator.process_stream`
- ❌ `processRequest` (not descriptive enough)
- ❌ `control_plane_process_request` (use dots, not underscores)

### 2. Attribute Naming Convention

Use `luthien.*` prefix for all custom attributes:

- ✅ `luthien.call_id`
- ✅ `luthien.policy.name`
- ❌ `call_id` (no prefix)
- ❌ `request.call_id` (wrong prefix)

See [otel-conventions.md](./otel-conventions.md) for full list.

### 3. Event vs Attribute

- **Attributes:** Metadata about the entire span (call_id, model, chunk_count)
- **Events:** Point-in-time occurrences (request_received, content_filtered, error)

Use attributes for searchable metadata, events for timeline markers.

### 4. Error Handling

Always record errors in spans:

```python
try:
    result = await risky_operation()
except Exception as e:
    span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
    span.record_exception(e)
    raise
```

### 5. Fire-and-Forget Redis Publishing

The `SimpleEventPublisher` uses `asyncio.create_task()` for non-blocking publishing. Never `await` the publish operation in critical paths.

---

## Data Retention

Current settings (see `observability/tempo-config.yaml` and `observability/loki-config.yaml`):

- **Traces:** 24 hours
- **Logs:** 24 hours

To change retention, edit the config files and restart:

```bash
./scripts/observability.sh restart
```

---

## Cleaning Up Data

### Remove all traces and logs

```bash
./scripts/observability.sh clean
```

This removes `observability/data/` which contains all Tempo and Loki data.

### Stop observability stack

```bash
./scripts/observability.sh down
```

---

## Migration Notes

### Breaking Changes

The migration from custom events to OpenTelemetry introduced these breaking changes:

1. **PolicyContext constructor**
   - OLD: `PolicyContext(call_id, emit_event: Callable)`
   - NEW: `PolicyContext(call_id, span: Span, event_publisher: SimpleEventPublisher | None)`

2. **ControlPlaneLocal constructor**
   - OLD: `ControlPlaneLocal(policy, redis_client: Redis | None)`
   - NEW: `ControlPlaneLocal(policy, event_publisher: SimpleEventPublisher | None)`

3. **Deleted classes**
   - `ActivityEvent` and all subclasses
   - `ActivityPublisher`
   - `PolicyEvent`

### What Was Removed

- `src/luthien_proxy/v2/activity/events.py` - All event classes
- `src/luthien_proxy/v2/activity/publisher.py` - ActivityPublisher
- `PolicyEvent` from `src/luthien_proxy/v2/control/models.py`
- `get_events()` method from control plane interface

### What Was Added

- `src/luthien_proxy/v2/telemetry.py` - OpenTelemetry configuration
- `src/luthien_proxy/v2/observability/bridge.py` - SimpleEventPublisher
- Span creation in gateway, control plane, and orchestrator
- `PolicyContext.emit()` now adds span events instead of creating PolicyEvents

---

## Further Reading

- [OpenTelemetry Python Docs](https://opentelemetry.io/docs/languages/python/)
- [Tempo Documentation](https://grafana.com/docs/tempo/latest/)
- [Grafana Trace-Logs Correlation](https://grafana.com/docs/grafana/latest/datasources/tempo/#trace-to-logs)
- [OTel Conventions Guide](./otel-conventions.md) (internal)

---

**Questions?** Check the [gotchas.md](./gotchas.md) or ask in the project chat.
