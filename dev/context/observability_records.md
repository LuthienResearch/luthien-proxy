# PipelineRecord System for Structured Observability

**Added:** 2025-01-14
**Updated:** 2025-01-14 - Renamed from LuthienPayloadRecord to PipelineRecord, simplified interface

## Overview

Simple, extensible system for logging structured data at any point in the request/response pipeline.

## Core Concepts

### LuthienRecord

Abstract base class for structured observability records. Each record:
- Has a `record_type` class-level constant (e.g., "pipeline")
- Includes `transaction_id` to track which transaction it belongs to
- Serializes automatically via `vars()` (no `to_dict()` method needed)

### PipelineRecord

Records payload data as it flows through the pipeline.

**Simple interface:**
- `transaction_id`: str - Which transaction this belongs to
- `pipeline_stage`: str - Identifier for this stage (e.g., "client_request", "backend_response")
- `payload`: str - String representation of the data

## Usage

```python
import json
from luthien_proxy.observability.context import PipelineRecord

# Log incoming client request
obs_ctx.record(PipelineRecord(
    transaction_id=call_id,
    pipeline_stage="client_request",
    payload=json.dumps(request_body)
))

# Log request after policy modification
obs_ctx.record(PipelineRecord(
    transaction_id=call_id,
    pipeline_stage="backend_request",
    payload=json.dumps(modified_request)
))

# Log format conversion
obs_ctx.record(PipelineRecord(
    transaction_id=call_id,
    pipeline_stage="format_conversion",
    payload=json.dumps({
        "from_format": "anthropic",
        "to_format": "openai",
        "result": openai_body
    })
))
```

## Integration with Existing Infrastructure

Records flow through `ObservabilityContext.record()` → `emit_event()` to:
- **Database**: Persistent storage via `emit_custom_event()`
- **Redis**: Real-time event stream via `RedisEventPublisher`
- **OTel Spans**: Trace correlation via `span.add_event()`

Event type is automatically set to `"luthien.{record.record_type}"` (e.g., `"luthien.pipeline"`).

### Common Queries

**All pipeline records:**
```logql
{app="luthien-gateway", record_type="pipeline"}
```

**Pipeline records by stage:**
```logql
{app="luthien-gateway", record_type="pipeline", payload_type="client_request"}
{app="luthien-gateway", record_type="pipeline", payload_type="backend_request"}
{app="luthien-gateway", record_type="pipeline", payload_type="client_response"}
```

**Follow a specific transaction:**
## Standard pipeline_stage Values

Use these consistent names across the codebase:

### Request Flow
- `client_request` - Raw request from client
- `format_conversion` - Format transformation (Anthropic ↔ OpenAI)
- `backend_request` - Final request sent to LLM backend (after policy)

### Response Flow
- `backend_response` - Raw response from LLM backend
- `client_response` - Final response sent to client (after policy)

### Streaming
- `stream_chunk` - Individual chunks during streaming

## Design Rationale

### Why "PipelineRecord"?

Clearer than "PayloadRecord" - emphasizes that this tracks data flowing through the pipeline.

### Why Simple (transaction_id, pipeline_stage, payload) Design?

- **All primitives**: No nested dicts, no serialization issues
- **Flexible**: Payload is just a string - serialize whatever you need
- **Queryable**: Both `record_type` and `pipeline_stage` are structured fields
- **Transaction-aware**: Every record knows which transaction it belongs to

### Why Include transaction_id in Record?

- Makes each record self-contained
- Ensures transaction context can't be lost/forgotten
- Simplifies code - don't need to track transaction separately

### Why vars() Instead of to_dict()?

- Simpler - no boilerplate methods
- Python's built-in `vars()` just returns `__dict__`
- Easy to serialize: `json.dumps(vars(record))`
- Extensible - new fields automatically included

## Changes from Original LuthienPayloadRecord

1. **Renamed**: `LuthienPayloadRecord` → `PipelineRecord`
2. **Added transaction_id**: Now required in constructor
3. **Simplified data format**:
   - Old: `stage` + arbitrary `data` dict
   - New: `pipeline_stage` + string `payload`
4. **Removed to_dict()**: Use `vars(record)` instead
5. **record_type changed**: `"payload"` → `"pipeline"`

## Future Extensions

Easy to add new record types by subclassing `LuthienRecord`:

```python
class PolicyDecisionRecord(LuthienRecord):
    record_type = "policy_decision"

    def __init__(self, transaction_id: str, policy_name: str, decision: str, rationale: str):
        super().__init__(transaction_id)
        self.policy_name = policy_name
        self.decision = decision
        self.rationale = rationale
```

## See Also

- [observability/context.py](../../src/luthien_proxy/observability/context.py) - Implementation
- [gateway_routes.py](../../src/luthien_proxy/gateway_routes.py) - Real-world usage examples
