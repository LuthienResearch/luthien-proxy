# Comprehensive Observability Architecture

**Created:** 2025-10-20
**Status:** Proposal - Architecture design for complete policy debugging

---

## Design Principles

1. **All four capabilities are essential**: Diff view, live streaming, payload inspection, performance analytics
2. **Accept 1-2 second latency** for non-live features (allows OTel batching)
3. **Each layer serves a distinct purpose** - don't try to make one tool do everything
4. **Minimize redundancy** - data should flow through clean pipelines, not duplicated channels

---

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        V2 Gateway (main.py)                      │
│  - Receives request                                              │
│  - Applies policy (stores before/after)                          │
│  - Calls LLM backend                                             │
│  - Applies policy to response (stores before/after)              │
└────────┬──────────────────────┬──────────────────────────────────┘
         │                      │
         │ OTel Spans           │ Payloads + Metadata
         │ (lightweight)        │ (full data)
         │                      │
         ▼                      ▼
┌──────────────────┐   ┌───────────────────────────────────────────┐
│  Tempo           │   │  PostgreSQL (new table: request_traces)  │
│  - Spans         │   │  - call_id (PK, links to trace_id)       │
│  - Attributes    │   │  - original_request (JSONB)               │
│  - Events        │   │  - final_request (JSONB)                  │
│  - Timing        │   │  - original_response (JSONB)              │
│  - 24h retention │   │  - final_response (JSONB)                 │
└──────────────────┘   │  - backend_model (TEXT)                   │
                       │  - policy_name (TEXT)                     │
                       │  - created_at (TIMESTAMP)                 │
                       │  - Indexes: call_id, backend_model, time  │
                       └───────────────────────────────────────────┘
         │                      │
         │                      │
         ▼                      ▼
┌──────────────────────────────────────────────────────────────────┐
│                         Grafana                                   │
│  Dashboard: "Policy Development"                                 │
│  - Panel: Recent requests (live, 1-2 sec delay)                  │
│  - Panel: Latency waterfall by span                              │
│  - Panel: Policy intervention rate                               │
│  - Panel: Backend comparison (side-by-side metrics)              │
│  - Links to detailed debug view (custom endpoint)                │
└──────────────────────────────────────────────────────────────────┘
         │
         │ Click "Debug Request abc123"
         ▼
┌──────────────────────────────────────────────────────────────────┐
│          GET /debug/request/<call_id>                         │
│  Unified debug view combining:                                   │
│  1. Full trace from Tempo (timing breakdown)                     │
│  2. Payloads from PostgreSQL (diff view)                         │
│  3. Policy events and decisions                                  │
│                                                                   │
│  Returns HTML with:                                              │
│  - Request diff (original → final)                               │
│  - Response diff (original → final)                              │
│  - Span waterfall (where time was spent)                         │
│  - Policy event timeline                                         │
│  - Copy-paste friendly JSON dumps                                │
└──────────────────────────────────────────────────────────────────┘


┌──────────────────────────────────────────────────────────────────┐
│  OPTIONAL: Redis + SSE (for live development only)               │
│  - /activity/stream endpoint (existing)                       │
│  - Lightweight events for "what's happening NOW"                 │
│  - Use during active policy development                          │
│  - NOT trying to replace other tools                             │
└──────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### During Request Processing

```python
# In main.py - chat_completions endpoint
with tracer.start_as_current_span("gateway.chat_completions") as span:
    call_id = str(uuid.uuid4())
    span.set_attribute("luthien.call_id", call_id)

    # 1. Capture original request
    original_request = RequestMessage(**data)

    # 2. Apply policy
    final_request = await control_plane.process_request(original_request, call_id)

    # 3. Store both versions in database
    await store_request_trace(
        call_id=call_id,
        original_request=original_request.model_dump(),
        final_request=final_request.model_dump(),
    )

    # 4. Add summary to span (not full payload - too big)
    if original_request != final_request:
        span.set_attribute("luthien.request.modified", True)
        span.set_attribute("luthien.request.diff_summary",
                          summarize_diff(original_request, final_request))

    # 5. Call backend
    response = await litellm.acompletion(**final_request.model_dump())

    # 6. Capture original response
    original_response = FullResponse.from_model_response(response)

    # 7. Apply policy
    final_response = await control_plane.process_full_response(original_response, call_id)

    # 8. Store both versions
    await update_request_trace(
        call_id=call_id,
        original_response=original_response.to_model_response().model_dump(),
        final_response=final_response.to_model_response().model_dump(),
    )

    # 9. Add summary to span
    if original_response != final_response:
        span.set_attribute("luthien.response.modified", True)
        span.set_attribute("luthien.response.diff_summary",
                          summarize_diff(original_response, final_response))
```

### Querying for Debugging

```python
# GET /debug/request/<call_id>
@app.get("/debug/request/{call_id}")
async def debug_request(call_id: str):
    # 1. Fetch payloads from PostgreSQL
    trace_data = await db.query(
        "SELECT * FROM request_traces WHERE call_id = $1", call_id
    )

    # 2. Query Tempo for spans (via Grafana API or direct query)
    spans = await tempo_client.query_trace_by_attribute("luthien.call_id", call_id)

    # 3. Build timing waterfall from spans
    waterfall = build_waterfall(spans)

    # 4. Compute diffs
    request_diff = compute_diff(
        trace_data['original_request'],
        trace_data['final_request']
    )
    response_diff = compute_diff(
        trace_data['original_response'],
        trace_data['final_response']
    )

    # 5. Render HTML with all information
    return templates.TemplateResponse("debug_request.html", {
        "call_id": call_id,
        "request_diff": request_diff,
        "response_diff": response_diff,
        "waterfall": waterfall,
        "spans": spans,
        "raw_data": trace_data,
    })
```

---

## Database Schema

```sql
-- New table: request_traces
CREATE TABLE request_traces (
    call_id UUID PRIMARY KEY,

    -- Request data
    original_request JSONB NOT NULL,
    final_request JSONB NOT NULL,
    request_modified BOOLEAN GENERATED ALWAYS AS (original_request != final_request) STORED,

    -- Response data
    original_response JSONB,
    final_response JSONB,
    response_modified BOOLEAN GENERATED ALWAYS AS (original_response != final_response) STORED,

    -- Metadata for querying/comparison
    backend_model TEXT NOT NULL,  -- e.g., "claude-opus-4-1"
    policy_name TEXT NOT NULL,    -- e.g., "NoOpPolicy"
    client_api TEXT NOT NULL,     -- e.g., "openai" or "anthropic"

    -- Timing (redundant with OTel, but useful for quick queries)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_ms INTEGER GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (completed_at - created_at)) * 1000
    ) STORED,

    -- Indexes for common queries
    CONSTRAINT valid_duration CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE INDEX idx_request_traces_created_at ON request_traces(created_at DESC);
CREATE INDEX idx_request_traces_backend_model ON request_traces(backend_model);
CREATE INDEX idx_request_traces_policy_name ON request_traces(policy_name);
CREATE INDEX idx_request_traces_modified ON request_traces(request_modified, response_modified);

-- For performance comparison queries
CREATE INDEX idx_request_traces_backend_comparison ON request_traces(
    backend_model, client_api, created_at DESC
);
```

---

## Implementation Plan

### Phase 1: Storage Layer (2-3 hours)

**Files to create:**
- `src/luthien_proxy/v2/storage/traces.py` - Database operations
- `prisma/schema.prisma` - Add `RequestTrace` model
- `migrations/YYYYMMDD_add_request_traces.sql` - SQL migration

**What it does:**
```python
# src/luthien_proxy/v2/storage/traces.py

async def store_request_trace(
    call_id: str,
    original_request: dict,
    final_request: dict,
    backend_model: str,
    policy_name: str,
    client_api: str,
) -> None:
    """Store request data for debugging and comparison."""

async def update_request_trace(
    call_id: str,
    original_response: dict,
    final_response: dict,
    completed_at: datetime,
) -> None:
    """Update trace with response data."""

async def get_request_trace(call_id: str) -> dict | None:
    """Retrieve full trace data for debugging."""

async def query_request_traces(
    backend_model: str | None = None,
    policy_name: str | None = None,
    modified_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    """Query traces for comparison and analysis."""
```

### Phase 2: Diff View & Debug Endpoint (2-3 hours)

**Files to create:**
- `src/luthien_proxy/debug/diff.py` - Diff computation
- `src/luthien_proxy/debug/endpoint.py` - Debug endpoint handler
- `src/luthien_proxy/v2/templates/debug_request.html` - Debug UI template
- `src/luthien_proxy/v2/static/debug.css` - Styling for diff view

**What it does:**
```python
# src/luthien_proxy/debug/diff.py

def compute_diff(original: dict, final: dict) -> DiffResult:
    """Compute structured diff between two JSON objects.

    Returns:
        DiffResult with:
        - added_fields: dict of fields present in final but not original
        - removed_fields: dict of fields present in original but not final
        - modified_fields: dict of fields with changed values
        - unchanged_fields: list of field names that didn't change
        - summary: human-readable string (e.g., "3 fields modified, 1 added")
    """

def summarize_diff(original: dict, final: dict) -> str:
    """Generate short summary for OTel span attribute."""
    # e.g., "messages: 5→3, temperature: 0.7→1.0"
```

**UI Features:**
- Side-by-side JSON view with syntax highlighting
- Color-coded diff (green = added, red = removed, yellow = modified)
- Expandable/collapsible JSON trees
- Copy button for each payload
- Download as JSON
- Link to full trace in Grafana

### Phase 3: Integrate with Gateway (1-2 hours)

**Files to modify:**
- `src/luthien_proxy/v2/main.py` - Add storage calls
- `src/luthien_proxy/v2/control/local.py` - Pass metadata through

**Changes:**
```python
# In main.py
from luthien_proxy.storage.traces import store_request_trace, update_request_trace

# In chat_completions endpoint:
# After process_request():
await store_request_trace(
    call_id=call_id,
    original_request=original_request.model_dump(),
    final_request=final_request.model_dump(),
    backend_model=final_request.model,
    policy_name=type(control_plane.policy).__name__,
    client_api="openai",  # or detect from endpoint
)

# After process_full_response():
await update_request_trace(
    call_id=call_id,
    original_response=original_response.to_model_response().model_dump(),
    final_response=final_response.to_model_response().model_dump(),
    completed_at=datetime.now(UTC),
)
```

### Phase 4: Performance Analytics (2 hours)

**Files to create:**
- `observability/grafana-dashboards/policy-development.json` - Grafana dashboard
- `src/luthien_proxy/v2/api/analytics.py` - Analytics endpoints

**Grafana Panels:**

1. **Recent Requests** (Table)
   - Columns: call_id, timestamp, backend_model, duration, modified
   - Click row → opens debug endpoint
   - Auto-refresh every 2 seconds

2. **Latency Breakdown** (Waterfall/Gantt chart)
   - Shows span hierarchy
   - Color-coded by component (gateway, policy, LLM, etc.)

3. **Policy Intervention Rate** (Time series)
   - % of requests modified vs passed through
   - Separate lines for request modification vs response modification

4. **Backend Comparison** (Bar chart)
   - Side-by-side latency for different backends
   - Filter by model, time range
   - P50/P95/P99 percentiles

**Analytics API:**
```python
# GET /v2/analytics/backend-comparison
@app.get("/v2/analytics/backend-comparison")
async def backend_comparison(
    start_time: datetime,
    end_time: datetime,
    backend_models: list[str] | None = None,
):
    """Compare performance across different backends.

    Returns:
        {
            "claude-opus-4-1": {
                "count": 150,
                "p50_ms": 1200,
                "p95_ms": 2400,
                "p99_ms": 3100,
                "error_rate": 0.02
            },
            "gpt-4": {...}
        }
    """
```

### Phase 5: Enhanced Live Monitoring (1 hour)

**Files to modify:**
- `src/luthien_proxy/v2/static/activity_monitor.html` - Enhance UI

**New Features:**
- Group events by call_id (collapsible sections)
- Show "modified" badge when request/response changed
- Click event → opens debug endpoint for that call_id
- Filter by event type, policy name
- Search by call_id

**Keep it simple:** This is for "what's happening NOW", not deep analysis.

### Phase 6: Tests & Documentation (2 hours)

**Test files:**
- `tests/unit_tests/v2/test_storage_traces.py` - Database operations
- `tests/unit_tests/v2/test_debug_diff.py` - Diff computation
- `tests/integration_tests/v2/test_debug_endpoint.py` - End-to-end debug flow
- `tests/e2e_tests/v2/test_policy_debugging.py` - Full workflow

**Documentation:**
- Update `dev/context/observability-guide.md` - Add new architecture
- Create `docs/POLICY_DEBUGGING.md` - Guide for policy developers
- Update `README.md` - Add link to debugging guide

---

## Total Time Estimate: 10-13 hours

Breakdown:
- Phase 1 (Storage): 2-3 hours
- Phase 2 (Diff view): 2-3 hours
- Phase 3 (Integration): 1-2 hours
- Phase 4 (Analytics): 2 hours
- Phase 5 (Live monitoring): 1 hour
- Phase 6 (Tests/docs): 2 hours

---

## Open Questions

1. **Storage retention**: How long should we keep request_traces data?
   - Option A: Same as Tempo (24h) - keeps things aligned
   - Option B: Configurable (e.g., 7 days for dev, 1 day for prod)
   - Option C: Based on disk space (auto-delete oldest when full)

2. **Payload size limits**: Full request/response can be large (especially with long conversations)
   - Option A: Store everything (could be 100KB+ per request)
   - Option B: Truncate large payloads (e.g., first 10KB)
   - Option C: Store separately in object storage (S3/local files)

3. **Privacy/security**: Request payloads may contain sensitive data
   - Option A: Only store when `LUTHIEN_DEBUG_MODE=true`
   - Option B: Hash/redact PII automatically
   - Option C: Add opt-out flag in config

4. **Tempo query integration**: How to fetch spans from Tempo?
   - Option A: Use Grafana API (simpler, already have Grafana)
   - Option B: Query Tempo directly (more control, less dependency)
   - Option C: Pre-fetch and cache in PostgreSQL (faster, redundant)

5. **Streaming support**: Current design assumes non-streaming. What about streaming responses?
   - Option A: Reconstruct full response from chunks before storing
   - Option B: Store chunk-by-chunk (harder to diff)
   - Option C: Store only final assembled response

---

## My Recommendations for Open Questions

1. **Retention: Option B** - Configurable with sane defaults (7 days dev, 24h prod)
2. **Size limits: Option A initially** - Store everything, optimize later if needed
3. **Privacy: Option A** - Only store in debug mode (default off in prod)
4. **Tempo: Option A** - Use Grafana API (simpler integration)
5. **Streaming: Option A** - Reconstruct full response (cleaner for diffing)

---

## Next Steps

If you approve this architecture, I'll:

1. Start with Phase 1 (storage layer) + Phase 3 (integration)
   - Gets the data flowing into the database
   - Can test with real requests immediately

2. Then Phase 2 (diff view)
   - Most important feature per your ranking
   - Builds on storage from Phase 1

3. Finally Phases 4-6 (analytics, monitoring, tests)
   - Polish and complete the system

Should I proceed with this plan?
