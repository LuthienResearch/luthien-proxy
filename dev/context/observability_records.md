# LuthienRecord System for Structured Observability

**Added:** 2025-01-14

## Overview

Simple, extensible system for logging structured data at any point in the request/response pipeline.

## Core Concepts

### LuthienRecord

Abstract base class for structured observability records. Each record type has:
- `record_type`: Class-level constant (e.g., "payload")
- `to_dict()`: Method to serialize to JSON for logging

### LuthienPayloadRecord

Simple atomic record: `(stage, data)` tuple.

- **stage**: String identifier for where in the pipeline this is logged (e.g., `"client.request"`, `"policy.modified"`)
- **data**: Arbitrary JSON-serializable dict containing whatever you want to log

## Usage

```python
from luthien_proxy.observability.context import LuthienPayloadRecord

# Log a request payload
obs_ctx.record(LuthienPayloadRecord(
    stage="client.request",
    data={
        "payload": request_body,
        "format": "anthropic",
        "endpoint": "/v1/messages"
    }
))

# Log a policy decision
obs_ctx.record(LuthienPayloadRecord(
    stage="policy.decision",
    data={
        "policy": "SimpleJudgePolicy",
        "decision": "modify",
        "modifications": {"added_system_message": True}
    }
))

# Log a format conversion
obs_ctx.record(LuthienPayloadRecord(
    stage="format.conversion",
    data={
        "from_format": "anthropic",
        "to_format": "openai",
        "input_payload": anthropic_body,
        "output_payload": openai_body
    }
))
```

## Integration with Existing Infrastructure

Records flow through `ObservabilityContext.emit_event()` to:
- **Loki**: Structured logs indexed by `stage`, `transaction_id`, `trace_id`
- **Database**: Persistent storage via `emit_custom_event()`
- **Redis**: Real-time event stream via `RedisEventPublisher`
- **OTel Spans**: Trace correlation via `span.add_event()`

Event type is automatically set to `"luthien.{record.record_type}"` (e.g., `"luthien.payload"`).

## Querying in Loki

```logql
# All records for a transaction
{service_name="luthien-proxy"} | json | transaction_id="abc-123"

# Just policy stages
{service_name="luthien-proxy"} | json | stage=~"policy.*"

# Compare before/after policy
{service_name="luthien-proxy"} | json
  | transaction_id="abc-123"
  | stage=~"policy.request.(before|after)"

# Format conversions
{service_name="luthien-proxy"} | json | stage="format.conversion"
```

## Recommended Stage Names

Use a dotted naming convention for clarity:

### Request Flow
- `client.request` - Raw request from client
- `format.conversion` - Format transformation (Anthropic ↔ OpenAI)
- `policy.request.before` - Request before policy modification
- `policy.request.after` - Request after policy modification
- `backend.request` - Final request sent to LLM backend

### Response Flow
- `backend.response` - Raw response from LLM backend
- `policy.response.before` - Response before policy modification
- `policy.response.after` - Response after policy modification
- `client.response` - Final response sent to client

### Streaming
- `stream.chunk.received` - Chunk received from backend
- `stream.chunk.sent` - Chunk sent to client

### Decisions
- `policy.decision` - Policy decision point
- `policy.intervention` - Policy intervention/blocking

## Design Rationale

### Why Not Ambient Context (ContextVars)?

Initially considered using `contextvars` to avoid passing `obs_ctx` everywhere, but decided against it:
- **Explicit > Implicit**: Passing `obs_ctx` makes dependencies clear
- **No Global State**: Avoids spooky action at a distance
- **Simpler**: One less abstraction layer to reason about

### Why Simple (stage, data) Design?

- **Atomic**: Just log something at a stage - no complex structure
- **Flexible**: Can log anything - payloads, decisions, metrics, etc.
- **Extensible**: Easy to add new record types later if needed
- **Queryable**: `stage` becomes indexed field in Loki

## Future Extensions

If we need more structure later, easy to add new record types:

```python
class LuthienPolicyDecisionRecord(LuthienRecord):
    record_type = "policy.decision"

    def __init__(self, policy_name: str, decision: str, ...):
        self.policy_name = policy_name
        self.decision = decision
        # ...
```

But start simple with just `LuthienPayloadRecord` and see what we actually need.

## See Also

- [observability/context.py](../../src/luthien_proxy/observability/context.py) - Implementation
- [examples/observability_usage.py](../../examples/observability_usage.py) - Usage examples
- [observability/README.md](../../observability/README.md) - Grafana/Loki/Tempo stack
