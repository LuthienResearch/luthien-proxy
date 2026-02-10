# V2 Observability

**When to use this guide:** You want to understand the observability architecture, implementation status, and design decisions.

**Last Updated:** 2025-10-20
**Status:** Complete ✅ (All phases 1-3 implemented)

**Other observability docs:**
- Need to view a trace? See [VIEWING_TRACES_GUIDE.md](VIEWING_TRACES_GUIDE.md)

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
- [x] Create `src/luthien_proxy/storage/events.py` with `emit_request_event()` and `emit_response_event()`
- [x] Create `src/luthien_proxy/storage/__init__.py`
- [x] Create `tests/unit_tests/test_storage_events.py`
- [x] Wire into `main.py` (startup/shutdown)

**Files**:
- `src/luthien_proxy/storage/events.py` - Event emission helpers
- `src/luthien_proxy/main.py` - Wired db_pool, added event emission
- `tests/unit_tests/test_storage_events.py` - Unit tests

#### Phase 1.2: OpenTelemetry Integration ✅
- [x] Add `call_id` as span attribute in all control plane operations
- [x] Verify spans are exported to Tempo with correct attributes
- [x] Test trace correlation via Grafana

**Files Modified**:
- `src/luthien_proxy/control/local.py` - Added call_id to all spans
- `src/luthien_proxy/telemetry.py` - Verified OTel setup

#### Phase 1.3: Streaming Event Emission ✅
- [x] Implement `reconstruct_full_response_from_chunks()` to build full response from streaming chunks
- [x] Add chunk buffering to `StreamingOrchestrator.process()` with `on_complete` callback
- [x] Modify `ControlPlaneLocal.process_streaming_response()` to buffer and emit events after streaming completes
- [x] Wire db_pool/redis_conn through streaming call chain
- [x] Write comprehensive unit tests for chunk reconstruction

**Files Modified**:
- `src/luthien_proxy/storage/events.py` - Added `reconstruct_full_response_from_chunks()`
- `src/luthien_proxy/control/streaming.py` - Added `on_complete` callback
- `src/luthien_proxy/control/local.py` - Added streaming event emission
- `src/luthien_proxy/main.py` - Passed db_pool/redis_conn to streaming
- `tests/unit_tests/test_storage_events.py` - Added 5 reconstruction tests

### ✅ Phase 2: Query & Debug Endpoints (COMPLETE)

**Goal**: Build REST API to query conversation events and compute diffs

#### Phase 2.1: Query Endpoints ✅
- [x] `GET /debug/calls/{call_id}` - Retrieve all events for a call
- [x] `GET /debug/calls/{call_id}/diff` - Compute structured diff of original vs final
- [x] `GET /debug/calls` - List recent calls with filters

#### Phase 2.2: Diff Computation ✅
- [x] Implement message-level diff for requests (added/removed/modified messages)
- [x] Implement content-level diff for responses (text changes, finish_reason)
- [x] Handle streaming response reconstruction for diff view (automatic via Phase 1.3)
- [x] Return Tempo trace link in response (`luthien.call_id` correlation)

**Files Created**:
- `src/luthien_proxy/debug/__init__.py` - Debug module exports
- `src/luthien_proxy/debug/routes.py` - Debug REST endpoints (430 lines)
- `tests/unit_tests/test_debug_routes.py` - Unit tests for debug endpoints (260 lines)

**Files**:
- `src/luthien_proxy/main.py` - Mounted debug router, wired db_pool

**Actual Time**: ~3 hours

### ✅ Phase 3.1: Diff UI (COMPLETE)

**Goal**: Build side-by-side diff viewer for policy transformations

- [x] Create HTML template with side-by-side diff view
- [x] Add automatic JSON detection and syntax highlighting
- [x] Link to Grafana trace from diff page
- [x] Browse recent calls interface
- [x] Query param support for direct links (`?call_id=...`)

**Files Created**:
- `src/luthien_proxy/static/diff_viewer.html` - Diff viewer UI (680 lines)
- Route: `/debug/diff` - Serves the diff viewer

**Features**:
- Side-by-side comparison of original vs final requests/responses
- Automatic JSON detection, pretty-printing, and syntax highlighting
- Visual indicators for changed vs unchanged content
- Metadata diffs (model, max_tokens, finish_reason)
- Message-level diffs for requests
- Content diffs for responses
- Direct link to Grafana Tempo traces
- Clickable recent calls list for easy navigation

**Actual Time**: ~1.5 hours

### ✅ Phase 3.2: Live Activity Dashboard (COMPLETE)

**Goal**: Integrate V2 events into real-time activity monitor

- [x] V2 events already published via `SimpleEventPublisher` (implemented in main.py)
- [x] Activity stream already wired to consume from Redis (`luthien:activity` channel)
- [x] Added filtering by call_id, model, event_type
- [x] Real-time filtering without interrupting stream
- [x] Retroactive filtering on stored events

**Files Modified**:
- `src/luthien_proxy/static/activity_monitor.html` - Added filter UI and logic

**Features**:
- Filter by call_id (substring match)
- Filter by model (substring match)
- Filter by event_type (exact match dropdown)
- Filters apply in real-time to live stream
- Stored events (up to 100) can be retroactively filtered
- Event type color coding for visual distinction

**Actual Time**: ~1 hour

### ✅ Phase 3.3: Grafana Dashboards (COMPLETE)

**Goal**: Create Grafana dashboards for V2 metrics and traces

- [x] Create dashboard showing V2 request rates by model
- [x] Add panels for policy execution latency (from OTel spans)
- [x] Add recent traces panel with call_id (linkable to debug endpoint)
- [x] Add request count and latency gauges
- [x] Add latency breakdown panel (gateway, policy, LLM)
- [x] Add errors & warnings log panel
- [x] Add links to diff viewer and activity monitor in dashboard

**Files Created**:
- `observability/grafana/dashboards/metrics.json` - Metrics & Performance dashboard

**Features**:
- **Request Rate by Model**: Time series showing requests/sec grouped by model (TraceQL rate query)
- **Policy Execution Latency (p95)**: 95th percentile latency for policy operations
- **Total Requests & Avg Latency**: Gauge panels for quick stats
- **Recent Traces Table**: Shows last 20 traces with call_id visible for linking
- **Errors & Warnings**: Log panel filtered to V2 errors/warnings
- **Latency Breakdown**: Stacked bars showing gateway, policy, and LLM latency
- **Quick Links**: Direct links to diff viewer and activity monitor from dashboard

**How to Use**:
1. Start observability stack: `./scripts/observability.sh start`
2. Open Grafana: http://localhost:3000
3. Navigate to "V2 Metrics & Performance" dashboard
4. Copy call_id from traces table → Open diff viewer → Paste call_id to see policy changes

**Actual Time**: ~1 hour

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

## Summary

**All observability phases complete!** The V2 gateway now has:

1. ✅ **Event Emission** - Request/response events persisted to PostgreSQL via background queue
2. ✅ **Debug API** - REST endpoints for querying events and computing diffs
3. ✅ **Diff Viewer UI** - Side-by-side comparison with JSON highlighting
4. ✅ **Activity Monitor** - Real-time event stream with filtering
5. ✅ **Grafana Dashboards** - Metrics, traces, and performance monitoring

**Total Implementation Time**: ~6.5 hours (vs estimated 8-10 hours)

**Next Steps** (future enhancements):
- Add more sophisticated filtering to activity monitor (regex, time range)
- Create alerting rules for policy failures or high latency
- Add Prometheus for more detailed metrics (if needed)
- Build policy-specific dashboards for custom policy implementations

---

## Historical Context

Previous planning documents (archived in `dev/context/`):
- `claude_observability_feedback.txt` - Initial feedback on avoiding duplicate storage
- `observability_review_summary.md` - Key architectural decisions
- `observability-architecture-proposal.md` - Original proposal with four-layer design

These documents informed the current architecture but are superseded by this unified plan.
