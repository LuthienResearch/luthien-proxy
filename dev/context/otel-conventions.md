# OpenTelemetry Conventions for Luthien Proxy

**Last Updated:** 2025-10-18
**Status:** Active - Use these conventions for all new OpenTelemetry instrumentation

---

## Purpose

This document defines naming conventions and attribute schemas for OpenTelemetry spans, events, and attributes in Luthien Proxy V2.

Consistency in naming allows:
- Easy searching in Grafana/Tempo
- Correlation across services
- Clear understanding of trace data

---

## Span Naming Convention

### Format

```
{module}.{operation}
```

Use lowercase with dots as separators.

### Examples

**Gateway spans:**
- `gateway.chat_completions` - Main request/response handler
- `gateway.request_received` - Initial request processing
- `gateway.response_sent` - Final response delivery

**Control plane spans:**
- `control_plane.process_request` - Request policy processing
- `control_plane.process_full_response` - Full response policy processing
- `control_plane.process_streaming_response` - Streaming response policy processing

**Orchestrator spans:**
- `orchestrator.start` - Streaming orchestration begins
- `orchestrator.complete` - Streaming orchestration completes
- `orchestrator.error` - Orchestration error

**LLM spans (future):**
- `llm.completion` - LLM API call
- `llm.streaming_chunk` - Single chunk received

**Policy spans:**
- Use policy name as module, e.g. `sql_protection.check_query`

---

## Span Attributes

### Core Attributes

All spans should include these when relevant:

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `luthien.call_id` | string | Unique request identifier | `"abc123def456"` |
| `luthien.endpoint` | string | API endpoint path | `"/v1/chat/completions"` |
| `luthien.model` | string | LLM model name | `"claude-opus-4"` |
| `luthien.stream` | boolean | Is streaming enabled | `true` |

### Gateway Attributes

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `luthien.gateway.api_key_hash` | string | Hash of API key (first 8 chars) | `"a1b2c3d4"` |
| `luthien.gateway.trace_id` | string | OTel trace ID | `"4bf92f3577b34da6a3ce929d0e0e4736"` |

### Control Plane Attributes

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `luthien.policy.name` | string | Policy class name | `"NoOpPolicy"` |
| `luthien.policy.success` | boolean | Did policy succeed | `true` |
| `luthien.policy.error` | string | Error message if failed | `"Timeout"` |

### Streaming Attributes

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `luthien.stream.chunk_count` | integer | Number of chunks | `42` |
| `luthien.stream.timeout_seconds` | float | Timeout duration | `30.0` |
| `luthien.stream.success` | boolean | Did streaming complete | `true` |

### Orchestrator Attributes

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `orchestrator.timeout_seconds` | float | Timeout setting | `30.0` |
| `orchestrator.chunk_count` | integer | Chunks processed | `15` |
| `orchestrator.success` | boolean | Completed successfully | `true` |

### LLM Attributes (future)

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `luthien.llm.provider` | string | LLM provider | `"anthropic"` |
| `luthien.llm.model` | string | Specific model | `"claude-opus-4"` |
| `luthien.llm.tokens.prompt` | integer | Prompt tokens | `100` |
| `luthien.llm.tokens.completion` | integer | Completion tokens | `200` |
| `luthien.llm.tokens.total` | integer | Total tokens | `300` |

---

## Span Events

### Event Naming Convention

Use `{module}.{event_name}` format, lowercase with dots.

### Common Events

**Gateway events:**
- `gateway.request_received` - Request arrived
- `gateway.response_sent` - Response sent
- `gateway.error` - Error occurred

**Control plane events:**
- `control_plane.policy_start` - Policy processing begins
- `control_plane.policy_complete` - Policy processing completes
- `control_plane.policy_error` - Policy error

**Orchestrator events:**
- `orchestrator.start` - Orchestration begins
- `orchestrator.chunk_received` - Chunk received from upstream
- `orchestrator.chunk_sent` - Chunk sent downstream
- `orchestrator.complete` - Orchestration completes
- `orchestrator.timeout` - Timeout occurred
- `orchestrator.error` - Error occurred

**Policy events (from PolicyContext.emit):**
- `policy.content_filtered` - Content was filtered/blocked
- `policy.request_modified` - Request was modified
- `policy.response_modified` - Response was modified
- `policy.tool_call_blocked` - Tool call was blocked
- `policy.sql_detected` - SQL injection detected
- `policy.error` - Policy error

### Event Attributes

Events can have their own attributes:

```python
span.add_event(
    "policy.content_filtered",
    attributes={
        "event.type": "policy.content_filtered",
        "event.summary": "Blocked harmful SQL",
        "event.severity": "warning",
        "event.reason": "sql_injection",
        "event.pattern": "DROP TABLE",
    }
)
```

Standard event attributes:

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `event.type` | string | Event type (same as event name) | `"policy.content_filtered"` |
| `event.summary` | string | Human-readable summary | `"Blocked harmful SQL"` |
| `event.severity` | string | Severity level | `"warning"` |
| `event.details.*` | various | Event-specific details | `{"reason": "sql_injection"}` |

---

## Severity Levels

Use these standard severity levels for events:

- `debug` - Detailed diagnostic information
- `info` - Informational messages (default)
- `warning` - Potentially harmful situations
- `error` - Error events

Example:

```python
ctx.emit(
    event_type="policy.content_filtered",
    summary="Blocked harmful content",
    severity="warning"  # Use standard severity
)
```

---

## Status Codes

Use OpenTelemetry status codes for spans:

```python
from opentelemetry import trace

# Success (default)
span.set_status(trace.Status(trace.StatusCode.OK))

# Error
span.set_status(trace.Status(trace.StatusCode.ERROR, "Timeout occurred"))

# Unset (for intermediate spans that don't have a clear success/failure)
span.set_status(trace.Status(trace.StatusCode.UNSET))
```

---

## Error Recording

Always record exceptions in spans:

```python
try:
    result = await risky_operation()
except Exception as e:
    # Record the exception with full stack trace
    span.record_exception(e)

    # Set span status to ERROR
    span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))

    # Optionally add error event
    span.add_event(
        "operation.error",
        attributes={
            "event.type": "error",
            "event.summary": str(e),
            "event.severity": "error",
        }
    )

    raise
```

---

## PolicyContext.emit() Convention

The `PolicyContext.emit()` method provides a standardized way to add events:

### Method Signature

```python
def emit(
    self,
    event_type: str,
    summary: str,
    details: Optional[dict[str, Any]] = None,
    severity: str = "info",
) -> None
```

### Usage Example

```python
ctx.emit(
    event_type="policy.content_filtered",
    summary="Blocked DROP TABLE statement",
    details={
        "reason": "sql_injection",
        "pattern": "DROP TABLE",
        "confidence": 0.95,
    },
    severity="warning"
)
```

### What It Does

1. Adds event to OTel span with all attributes
2. Optionally publishes to Redis for real-time UI
3. Flattens `details` dict into individual `event.*` attributes

### Event Attribute Flattening

```python
# Input
ctx.emit(
    event_type="policy.tool_call_blocked",
    summary="Blocked risky tool call",
    details={"tool": "exec", "reason": "dangerous"},
    severity="error"
)

# Resulting span event attributes
{
    "event.type": "policy.tool_call_blocked",
    "event.summary": "Blocked risky tool call",
    "event.severity": "error",
    "event.tool": "exec",  # flattened from details
    "event.reason": "dangerous",  # flattened from details
}
```

---

## Trace Context Propagation

OpenTelemetry automatically propagates trace context via HTTP headers:

- `traceparent` - W3C Trace Context header
- `tracestate` - Additional vendor-specific data

You don't need to manually propagate context between services - OTel handles it.

### Accessing Current Span

```python
from opentelemetry import trace

# Get current span (if any)
current_span = trace.get_current_span()

# Check if span is recording
if current_span.is_recording():
    current_span.set_attribute("custom.attr", "value")
```

---

## Correlation with Logs

The `TraceContextFormatter` in `telemetry.py` automatically adds trace context to all log records:

```python
# Log format includes trace_id and span_id
logger.info("Processing request")

# Output:
# 2024-01-15 10:30:00 INFO [trace_id=4bf92f3577b34da6a3ce929d0e0e4736 span_id=00f067aa0ba902b7] Processing request
```

This allows Grafana to correlate logs → traces.

---

## Redis Event Schema

Events published to Redis via `SimpleEventPublisher` follow this schema:

```json
{
  "call_id": "abc123",
  "event_type": "policy.content_filtered",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "summary": "Blocked harmful content",
    "severity": "warning",
    "reason": "sql_injection",
    "pattern": "DROP TABLE"
  }
}
```

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `call_id` | string | Yes | Request identifier |
| `event_type` | string | Yes | Event type (matches span event name) |
| `timestamp` | string (ISO 8601) | Yes | Event timestamp in UTC |
| `data` | object | No | Event-specific data (summary, severity, details) |

---

## Searching Traces in Grafana

### By Call ID

```
{luthien.call_id="abc123"}
```

### By Policy Name

```
{luthien.policy.name="SQLProtectionPolicy"}
```

### By Service

```
{service.name="luthien-proxy-v2"}
```

### By Span Name

```
{name="control_plane.process_request"}
```

### By Error Status

```
{status=error}
```

### Combining Filters

```
{luthien.policy.name="SQLProtectionPolicy" && status=error}
```

---

## Do's and Don'ts

### ✅ Do

- Use `luthien.*` prefix for all custom attributes
- Use lowercase with dots for span/event names
- Include `call_id` in all spans
- Record exceptions with `span.record_exception(e)`
- Set span status on errors
- Use standard severity levels (debug, info, warning, error)
- Add events for point-in-time occurrences
- Add attributes for span-wide metadata

### ❌ Don't

- Use underscores in span names (`control_plane_process` ❌)
- Omit the `luthien.` prefix (`call_id` ❌, use `luthien.call_id` ✅)
- Use generic names (`process`, `handler` ❌)
- Create spans for trivial operations (< 1ms)
- Add high-cardinality attributes (e.g., full message content)
- Block on Redis publishing (`await` on `publish_event()` ❌)
- Add sensitive data to spans (API keys, passwords, PII)

---

## Future Extensions

### Metrics (Not Yet Implemented)

OpenTelemetry metrics could track:

- Request rate (requests/sec)
- Response latency (p50, p95, p99)
- Error rate (errors/sec)
- Policy block rate (blocks/sec)
- Token usage (tokens/sec)

### Baggage (Not Yet Implemented)

OpenTelemetry baggage could propagate:

- User ID
- Organization ID
- Feature flags
- A/B test groups

---

## Examples

### Gateway Span

```python
from luthien_proxy.v2.telemetry import tracer

with tracer.start_as_current_span("gateway.chat_completions") as span:
    span.set_attribute("luthien.call_id", call_id)
    span.set_attribute("luthien.endpoint", "/v1/chat/completions")
    span.set_attribute("luthien.model", "claude-opus-4")
    span.set_attribute("luthien.stream", True)

    span.add_event("gateway.request_received")

    # ... process request ...

    span.add_event("gateway.response_sent")
```

### Control Plane Span

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("control_plane.process_request") as span:
    span.set_attribute("luthien.call_id", call_id)
    span.set_attribute("luthien.policy.name", policy.__class__.__name__)

    try:
        ctx = PolicyContext(call_id, span, event_publisher)
        result = await policy.process_request(request, ctx)

        span.set_attribute("luthien.policy.success", True)
        return result

    except Exception as e:
        span.record_exception(e)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
        span.set_attribute("luthien.policy.success", False)
        raise
```

### Policy Event

```python
# In policy code
async def process_request(self, request: Request, ctx: PolicyContext) -> Request:
    # Check for SQL injection
    if self._contains_sql(request):
        ctx.emit(
            event_type="policy.sql_detected",
            summary=f"Detected SQL pattern: {pattern}",
            details={
                "pattern": pattern,
                "confidence": 0.95,
                "action": "blocked",
            },
            severity="warning"
        )

        raise ValueError("SQL injection detected")

    return request
```

---

## Reference Implementation

See these files for reference implementations:

- [src/luthien_proxy/v2/telemetry.py](../../src/luthien_proxy/v2/telemetry.py) - OTel setup
- [src/luthien_proxy/v2/main.py](../../src/luthien_proxy/v2/main.py) - Gateway spans
- [src/luthien_proxy/v2/control/local.py](../../src/luthien_proxy/v2/control/local.py) - Control plane spans
- [src/luthien_proxy/v2/policies/context.py](../../src/luthien_proxy/v2/policies/context.py) - PolicyContext.emit()
- [src/luthien_proxy/v2/observability/bridge.py](../../src/luthien_proxy/v2/observability/bridge.py) - Redis events

---

**Questions?** See [observability-guide.md](./observability-guide.md) for usage guide.
