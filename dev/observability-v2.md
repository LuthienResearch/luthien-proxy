# V2 Observability

**Last Updated:** 2025-10-20
**Status:** Phases 1-2 Complete ✅ (Event emission + Debug endpoints implemented)

---

## Overview

V2 observability provides comprehensive debugging and monitoring for policy decisions. It reuses existing V1 infrastructure (`conversation_events` table, Redis pub/sub) but wires it directly into the V2 gateway instead of relying on LiteLLM callbacks.

### Key Capabilities

1. **Before/After Diff View** - Compare original vs policy-transformed requests/responses
2. **Live Activity Stream** - Real-time monitoring via Redis pub/sub
3. **Full Payload Inspection** - Query complete request/response data from PostgreSQL
4. **Performance Analytics** - Trace timing via OpenTelemetry + Tempo

---

## Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                      V2 Gateway (main.py)                       │
│  - Generate call_id at boundary                                 │
│  - Emit events via storage/events.py helpers                    │
│  - OpenTelemetry spans with call_id attribute                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ├─────────────────────────────────┐
                              │                                 │
                              ▼                                 ▼
            ┌─────────────────────────────┐   ┌────────────────────────────┐
            │  Background Task Queue      │   │  OpenTelemetry SDK         │
            │  (CONVERSATION_EVENT_QUEUE) │   │  (via telemetry.py)        │
            └─────────────────────────────┘   └────────────────────────────┘
                      │                                    │
                      ▼                                    ▼
    ┌─────────────────────────────────┐   ┌────────────────────────────────┐
    │  PostgreSQL                      │   │  Tempo (via Grafana)           │
    │  (conversation_events table)     │   │  - Spans with luthien.call_id  │
    │  - Before/after payloads         │   │  - Performance traces          │
    │  - Queryable by call_id          │   │  - Grafana dashboards          │
    └─────────────────────────────────┘   └────────────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────┐
    │  Redis Pub/Sub (optional)       │
    │  - Real-time activity stream    │
    │  - Live dashboard updates       │
    └─────────────────────────────────┘
```

### Event Types

V2 emits these conversation events:

- `v2_request` - Original and final (post-policy) request
- `v2_response` - Original and final (post-policy) response (both streaming and non-streaming)

Each event contains:
```json
{
  "call_id": "uuid-string",
  "event_type": "v2_request" | "v2_response",
  "timestamp": "ISO-8601",
  "payload": {
    "data": {"model": "...", "messages": [...], ...},  // for request
    "response": {"choices": [...], ...}                 // for response
  }
}
```

### Storage Strategy

- **PostgreSQL** (`conversation_events`): Stores complete payloads for diffing and inspection
- **Tempo**: Stores timing/span data for performance analysis
- **Redis**: Optional real-time pub/sub for live dashboards

**Non-blocking**: All persistence happens via background task queue to keep request path fast.

---

## Implementation Status

### ✅ Phase 1: Core Event Emission (COMPLETE)

**Goal**: Wire V2 gateway to emit conversation events for all requests/responses

#### Phase 1.1: Non-Streaming Events ✅
- [x] Create `src/luthien_proxy/v2/storage/events.py` with `emit_request_event()` and `emit_response_event()`
- [x] Wire database pool into V2 gateway (`main.py`)
- [x] Emit request events (original + final) in `/v1/chat/completions`
- [x] Emit response events (original + final) for non-streaming responses
- [x] Write unit tests for event emission helpers

**Files Modified**:
- `src/luthien_proxy/v2/storage/events.py` - Event emission helpers
- `src/luthien_proxy/v2/main.py` - Wired db_pool, added event emission
- `tests/unit_tests/v2/test_storage_events.py` - Unit tests

#### Phase 1.2: OpenTelemetry Integration ✅
- [x] Add `call_id` as span attribute in all control plane operations
- [x] Verify spans are exported to Tempo with correct attributes
- [x] Test trace correlation via Grafana

**Files Modified**:
- `src/luthien_proxy/v2/control/local.py` - Added call_id to all spans
- `src/luthien_proxy/v2/telemetry.py` - Verified OTel setup

#### Phase 1.3: Streaming Event Emission ✅
- [x] Implement `reconstruct_full_response_from_chunks()` to build full response from streaming chunks
- [x] Add chunk buffering to `StreamingOrchestrator.process()` with `on_complete` callback
- [x] Modify `ControlPlaneLocal.process_streaming_response()` to buffer and emit events after streaming completes
- [x] Wire db_pool/redis_conn through streaming call chain
- [x] Write comprehensive unit tests for chunk reconstruction

**Files Modified**:
- `src/luthien_proxy/v2/storage/events.py` - Added `reconstruct_full_response_from_chunks()`
- `src/luthien_proxy/v2/control/streaming.py` - Added `on_complete` callback
- `src/luthien_proxy/v2/control/local.py` - Added streaming event emission
- `src/luthien_proxy/v2/main.py` - Passed db_pool/redis_conn to streaming
- `tests/unit_tests/v2/test_storage_events.py` - Added 5 reconstruction tests

### ✅ Phase 2: Query & Debug Endpoints (COMPLETE)

**Goal**: Build REST API to query conversation events and compute diffs

#### Phase 2.1: Query Endpoints ✅
- [x] `GET /v2/debug/calls/{call_id}` - Retrieve all events for a call
- [x] `GET /v2/debug/calls/{call_id}/diff` - Compute structured diff of original vs final
- [x] `GET /v2/debug/calls` - List recent calls with filters

#### Phase 2.2: Diff Computation ✅
- [x] Implement message-level diff for requests (added/removed/modified messages)
- [x] Implement content-level diff for responses (text changes, finish_reason)
- [x] Handle streaming response reconstruction for diff view (automatic via Phase 1.3)
- [x] Return Tempo trace link in response (`luthien.call_id` correlation)

**Files Created**:
- `src/luthien_proxy/v2/debug/__init__.py` - Debug module exports
- `src/luthien_proxy/v2/debug/routes.py` - Debug REST endpoints (430 lines)
- `tests/unit_tests/v2/test_debug_routes.py` - Unit tests for debug endpoints (260 lines)

**Files Modified**:
- `src/luthien_proxy/v2/main.py` - Mounted debug router, wired db_pool

**Actual Time**: ~3 hours

### 🔄 Phase 3: UI & Dashboards (TODO)

**Goal**: Visualize diffs and traces in user-friendly interfaces

#### Phase 3.1: Diff UI
- [ ] Create HTML template for side-by-side diff view
- [ ] Add syntax highlighting for JSON payloads
- [ ] Link to Grafana trace from diff page

#### Phase 3.2: Live Activity Dashboard
- [ ] Reuse existing `SimpleEventPublisher` for V2 events
- [ ] Update activity dashboard to show V2 events
- [ ] Add filtering by call_id, model, event_type

#### Phase 3.3: Grafana Dashboards
- [ ] Create dashboard showing V2 request rates by model
- [ ] Add panel for policy execution latency (from OTel spans)
- [ ] Link to debug endpoint from Grafana (via call_id)

**Estimated Time**: 2-3 hours

---

## Key Design Decisions

### 1. Reuse V1 Infrastructure

**Decision**: Use existing `conversation_events` table and task queue instead of creating new tables.

**Rationale**:
- Avoids duplicate storage
- Proven non-blocking pattern (background task queue)
- Same query patterns work for V1 and V2

**Trade-off**: Event types distinguish V1 vs V2 (`v2_request`, `v2_response` vs `request`, `response`)

### 2. Non-Blocking Event Emission

**Decision**: All event emission goes through `CONVERSATION_EVENT_QUEUE` (background task queue).

**Rationale**:
- Keeps request path fast (no blocking DB writes)
- Failures in persistence don't fail requests
- Aligns with "fail fast" philosophy

**Implementation**:
- `emit_request_event()` and `emit_response_event()` submit to queue and return immediately
- Streaming events emitted via `on_complete` callback after stream finishes

### 3. Streaming Chunk Reconstruction

**Decision**: Buffer chunks passively during streaming, reconstruct full response after completion.

**Rationale**:
- Can't emit streaming events until we have the complete response
- Passive buffering (copy during yield) has minimal overhead
- Reconstruction happens in background callback (non-blocking)

**Trade-off**: Memory overhead for buffering chunks (acceptable for typical response sizes)

### 4. OpenTelemetry for Performance

**Decision**: Use OTel spans for timing, PostgreSQL for payload storage.

**Rationale**:
- OTel excels at distributed tracing and timing
- PostgreSQL excels at structured queries and payload inspection
- Each tool serves its purpose (don't force one tool to do everything)

**Implementation**:
- `call_id` links OTel spans to conversation events
- Grafana queries both Tempo (spans) and PostgreSQL (payloads)

---

## Testing Strategy

### Unit Tests
- ✅ Event emission helpers (`test_storage_events.py`)
- ✅ Chunk reconstruction (`test_reconstruct_*` tests)
- ⏳ Diff computation (Phase 2)
- ⏳ Query endpoint handlers (Phase 2)

### Integration Tests
- ⏳ End-to-end event flow (request → event emission → PostgreSQL)
- ⏳ Streaming event flow (chunks → reconstruction → event emission)
- ⏳ OTel span correlation with conversation events

### E2E Tests
- ⏳ Full request cycle with policy transformation
- ⏳ Verify diff endpoint returns correct before/after
- ⏳ Verify Tempo trace link works

---

## Debugging

### Verify Event Emission

```bash
# Query recent V2 events
uv run python scripts/query_debug_logs.py --call-id <call_id>

# Check background task queue health
# (Add monitoring endpoint in Phase 2)
```

### Verify OTel Spans

```bash
# Open Grafana
open http://localhost:3000

# Navigate to Tempo data source
# Search for trace with luthien.call_id=<call_id>
```

### Common Issues

**Events not appearing in PostgreSQL**:
1. Check db_pool is initialized in `main.py`
2. Verify `CONVERSATION_EVENT_QUEUE` is processing
3. Check logs for task queue exceptions

**Spans missing call_id**:
1. Verify `call_id` is generated at gateway boundary
2. Check span attributes in control plane operations
3. Confirm OTel exporter is configured correctly

---

## Next Steps

1. **Phase 2.1**: Implement query endpoints (`/v2/debug/calls/...`)
2. **Phase 2.2**: Implement diff computation logic
3. **Phase 3.1**: Build diff UI with side-by-side view
4. **Phase 3.2**: Wire V2 events into live activity dashboard
5. **Phase 3.3**: Create Grafana dashboards for V2 metrics

**Estimated Total Remaining**: 5-7 hours

---

## Historical Context

Previous planning documents (archived in `dev/context/`):
- `claude_observability_feedback.txt` - Initial feedback on avoiding duplicate storage
- `observability_review_summary.md` - Key architectural decisions
- `observability-architecture-proposal.md` - Original proposal with four-layer design

These documents informed the current architecture but are superseded by this unified plan.
