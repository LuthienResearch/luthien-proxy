# Project Audit: luthien-proxy

**Date:** 2026-03-05 (updated 2026-03-05 — observability section + GitHub cross-reference added)
**Commit:** 53d9aff (main)
**Methodology:** 8 parallel explore agents + 2 librarian (OSS benchmark + observability research) + Oracle architectural review + direct static analysis scans
**Context:** Public demo planned for April 16, 2026 (6 weeks). Goal: wide adoption at scale.

---

## Status Update — 2026-03-31 (T-16 days to demo)

**Overall: On track for a viable demo, but 4 Tier 1 items have no PR yet and need owners.**

### Tier 1 — Must-Have Before Demo

| # | Finding | Status | Detail |
|---|---------|--------|--------|
| 1 | Cache AnthropicClient by credential hash | ✅ Done | PR #312 merged 2026-03-22. `llm/anthropic_client_cache.py` — LRU cache keyed by credential hash, wired into `gateway_routes.py`. |
| 2 | Sentry error tracking | 🔄 In review | PR #335 open. Needs Peter re-review. |
| 3 | Prometheus metrics + `/metrics` endpoint | ❌ Not started | No PR. Assigned to Paolo. |
| 4 | Rich health checks (DB, Redis) | ❌ Not started | No PR. Assigned to Paolo. |
| 5 | Sanitize headers in DebugLoggingPolicy | ❌ Not started | Branch `fix/sanitize-debug-logging-headers` exists, no PR open. |
| 6 | Error detail leakage (raw exceptions in HTTP responses) | 🔄 In review | PR #313 open (18+ days). |
| 7 | Client disconnect detection in streaming | 🔄 In review | PR #465 (cancel upstream) + PR #466 (finally block fix), stacked. |
| 8 | Forward anthropic-* headers upstream | ✅ Done | PR #269 merged. Finding fully addressed. |

**Tier 1 summary: 2 done, 3 in review, 3 not started.**
The 3 not-started items (#3, #4, #5) are the remaining gap before demo day.

### Tier 2 — Should-Have Before Demo

| # | Finding | Status | Detail |
|---|---------|--------|--------|
| 9 | Custom LLM metrics + Grafana dashboard | 🟡 Partial | PR #460 merged: external Grafana Cloud dashboard via Cloudflare Worker. Local Prometheus `/metrics` endpoint still missing (see Tier 1 #3). |
| 10 | structlog structured logging | ❌ Not started | No PR. |
| 11 | Alerting rules (Grafana + Sentry → Slack) | 🟡 Partial | Sentry alerts covered by PR #335 if merged. Grafana alerting not started. |
| 12 | Rate limiting | ❌ Not started | No PR. |
| 13 | Extract shared judge logic from duplicated policies | ❌ Not started | No PR. |
| 14 | Return errors in upstream API format | ❌ Not started | No PR. |
| 15 | Forward Anthropic cache usage tokens | 🔄 In review | PR #463 open. |
| 16 | Pipeline Redis operations (4 round-trips → 1-2) | ❌ Not started | No PR. |
| 17 | Allowlist `__import__()` before dynamic policy load | 🔄 In review | PR #316 (covers both #17 and #18). |
| 18 | Replace `eval()` with AST-based resolution | 🔄 In review | PR #316. |
| 19 | Multi-stage Docker build | ❌ Not started | No PR. |

### Positive Surprises Since March 5

- **Dual API Path (Section 2.3, Systemic Risk HIGH)** — addressed ahead of schedule. PR #421 (`refactor: unify policy interface to hooks-only`) eliminated the OpenAI hook-based / Anthropic execution-oriented split. This was a Tier 4 item; it's done.
- **Type safety (Section 5.2)** — PR #461 (`refactor: type strictness pass`) reduced `Any` usage significantly.
- **ToolCallJudgePolicy tests (Section 6.2)** — PR #448 added unit tests for the largest/most complex policy.
- **Dead code removal (Section 5.4)** — PR #445 removed dead code from ToolCallJudgePolicy.

### What Needs Owners Now

These Tier 1 items have no PR and are blocking demo readiness:

1. **Prometheus `/metrics`** (#3) — Paolo assigned. ~2-4 hours of work.
2. **Rich health checks** (#4) — Paolo assigned. ~1-2 hours of work.
3. **Sanitize debug logging headers** (#5) — branch exists, needs PR opened. Trivial fix.

---

## 1. Executive Summary

luthien-proxy is an AI Control gateway — a FastAPI proxy between Claude Code/Codex and LLM APIs (OpenAI + Anthropic). It intercepts every request/response, applies configurable policies (block, transform, judge), and logs everything. Python 3.13 + LiteLLM + asyncio.

**Strengths:**
- Clean layering with no runtime circular dependencies
- Strong streaming pipeline (TaskGroup, bounded queues, safe_put circuit breaker) — stronger than LiteLLM and Portkey
- 87% test coverage with 1,126 passing tests
- Well-documented architecture and gotchas
- Pyright basic mode: 0 errors, 0 warnings

**Key risks:**
- 1 CRITICAL performance issue (new HTTP client per passthrough request)
- 3 HIGH security findings (API key leakage, error detail exposure, no rate limiting)
- No error tracking, no metrics, no alerting — the gateway cannot detect its own failures at scale (Section 10)
- Activity Monitor broken — events never reach Redis pub/sub (discovered by external QA, Section 12.3.1)
- Dual API path (OpenAI hook-based vs Anthropic execution-oriented) is systemic technical debt
- ~300 lines of code duplication between two core policies

**Codebase statistics:**
- 93 source files, 16,317 lines, 619 functions, 146 classes
- 29 `type: ignore`, 138 `Any` usages, 73 `cast()` usages
- 25 functions >40 lines (largest: 191 lines)
- 26 `noqa` suppressions across 7 files

---

## 2. Architecture & Dependency Analysis

### 2.1 Layering

Clean unidirectional dependency flow:
```
main → pipeline → orchestration → streaming → policy_core → llm/utils
```

No runtime circular dependencies. `TYPE_CHECKING` guards used correctly throughout.

### 2.2 LiteLLM Type Coupling (MEDIUM)

`litellm.types.utils.ModelResponse` used directly in 26 files across 5 modules (`pipeline`, `orchestration`, `streaming`, `policies`, `llm`). This creates tight coupling to LiteLLM internals.

**Recommendation:** Consolidate imports through a re-export module (e.g., `llm/types/`). Do NOT wrap — LiteLLM types are the canonical representation.

### 2.3 Dual API Path — Systemic Risk (HIGH)

Two completely different execution models:
- **OpenAI:** Hook-based policies (`OpenAIPolicyInterface` with 10 abstract hooks)
- **Anthropic:** Execution-oriented (`AnthropicExecutionInterface` with `run_anthropic(io, ctx)`)

29 `type: ignore` comments in multi-policy composition (`multi_serial_policy.py`: 12, `multi_parallel_policy.py`: 4). This architecture won't scale cleanly to a third API format.

### 2.4 Module-Level Side Effects (LOW)

`main.py:48-50`: `configure_tracing()`, `configure_logging()`, `instrument_redis()` run at import time inside `create_app()`. This is acceptable for an app factory but complicates testing — any test importing the module triggers side effects.

---

## 3. Security Vulnerabilities

### 3.1 API Key Leakage in DebugLoggingPolicy (HIGH)

**File:** `policies/debug_logging_policy.py:67-78`

`DebugLoggingPolicy` logs full HTTP headers (including `Authorization` headers containing API keys) to both stdout and the database. `sanitize_headers()` exists in the codebase but is not used in this code path.

**Fix:** Apply `sanitize_headers()` before logging. Filter `Authorization`, `x-api-key`, and similar headers.

### 3.2 Error Detail Leakage (HIGH)

8 instances of raw exception messages exposed in `HTTPException.detail` fields:

| File | Lines | Context |
|------|-------|---------|
| `admin/routes.py` | 175, 243 | Admin endpoints |
| `debug/routes.py` | 64, 95, 123 | Debug endpoints |
| `request_log/routes.py` | 62, 81 | Request log endpoints |
| `pipeline/anthropic_processor.py` | 594 | Mid-stream error |
| `gateway_routes.py` | 50-76 | Auth/validation errors |

**Fix:** Log full exception server-side, return generic error messages to clients.

### 3.3 eval() and __import__() Usage (MEDIUM)

- `admin/policy_discovery.py:134`: `eval()` on type annotation strings
- `config.py:114`: `__import__()` for dynamic policy loading

Oracle downgraded from HIGH to MEDIUM — targets are type annotations and configured class paths, not user input. However, admin API input reaches `__import__()` via policy class references.

**Fix:** Allowlist-validate class references before dynamic import. Replace `eval()` with `ast.literal_eval()` or direct type resolution.

### 3.4 No Rate Limiting (HIGH)

No rate limiting exists anywhere in the gateway. This is the single biggest gap vs. production LLM gateways (LiteLLM, Portkey, Kong all have it). The proxy is open to abuse if exposed beyond localhost.

**Fix:** Add per-key rate limiting. Start with a simple token bucket on the `/v1/` routes.

### 3.5 Additional Security Notes

- `session.py:132-171`: No CSRF protection on session endpoints
- `session.py:202`: Dev key hint in error message (acceptable for dev tool)
- `telemetry.py:111`: `insecure=True` on OTLP exporter (acceptable for local dev)
- `admin/policy_discovery.py:135,176,260`: 3 bare `except: pass` blocks that silently swallow errors
- `admin/routes.py:202-238`: Returns HTTP 200 with `{"success": false}` — masks errors from monitoring

---

## 4. Performance Bottlenecks

### 4.1 New AnthropicClient Per Passthrough Request (CRITICAL)

**File:** `gateway_routes.py:104,111,112`

Every passthrough request (Claude Code's default auth mode) creates a new `AnthropicClient` → new `httpx.AsyncClient` → full TCP+TLS handshake (~50-100ms overhead per request). Claude Code uses passthrough auth for every request.

**Fix:** Cache `AnthropicClient` instances by credential hash (e.g., `hashlib.sha256(api_key.encode()).hexdigest()`). Use `WeakValueDictionary` or TTL-based eviction.

### 4.2 No Client Disconnect Detection in Streaming (HIGH)

When a client disconnects mid-stream, the gateway continues consuming the upstream LLM response (and paying for it). Extended thinking responses can run 30+ seconds.

**Fix:** Check `request.is_disconnected()` periodically in the streaming loop. Cancel the upstream request on disconnect.

### 4.3 Triple Chunk Buffering (MEDIUM)

Three separate buffers accumulate the same streaming data:
1. `StreamState.raw_chunks` (list — unbounded)
2. `TransactionRecorder._ingress_chunks` (list)
3. `TransactionRecorder._egress_chunks` (list)

**Fix:** `raw_chunks` can be `deque(maxlen=1)` — only latest chunk needed for state. Or remove entirely if TransactionRecorder covers the logging need.

### 4.4 Redis Round-Trips (MEDIUM)

**File:** `credential_manager.py:196-202, 296-315`

4 sequential Redis round-trips per authenticated request in the credential cache hit path.

**Fix:** Use Redis pipeline (`MULTI`/`EXEC`) to batch operations into 1-2 round-trips.

### 4.5 Additional Performance Notes

- `observability/emitter.py:221`: `print()` bypass (instead of logging) — minor
- `streaming/streaming_chunk_assembler.py:68`: Debug-level logging on every chunk — overhead in hot path
- `orchestration/policy_orchestrator.py:93,178`: `copy.deepcopy()` on request/response objects — expensive for large payloads

---

## 5. Code Quality & Maintainability

### 5.1 Policy Code Duplication (HIGH)

~300 lines of nearly identical code between:
- `policies/tool_call_judge_policy.py` (984 lines — largest file)
- `policies/dogfood_safety_policy.py` (523 lines)

Shared functionality: judge prompt construction, LLM call logic, response parsing, tool call evaluation.

**Recommendation (Oracle):** Extract shared utility functions (not a base class or mixin). Keep policies as flat compositions of reusable functions.

### 5.2 Type Safety Gaps (MEDIUM)

| Metric | Count |
|--------|-------|
| `type: ignore` | 29 across 7 files |
| `Any` usage | 138 across codebase |
| `cast()` usage | 73 across codebase |
| `noqa` suppressions | 26 across 7 files |

Pyright basic mode reports 0 errors — but basic mode doesn't catch many issues that strict mode would.

### 5.3 Large Functions (MEDIUM)

25 functions exceed 40 lines. Top offenders:

| Function | File | Lines |
|----------|------|-------|
| `process_llm_request` | `pipeline/processor.py` | 191 |
| `run_anthropic` | `policies/tool_call_judge_policy.py` | ~150 |
| `_evaluate_tool_calls` | `policies/tool_call_judge_policy.py` | ~120 |
| `process_anthropic_request` | `pipeline/anthropic_processor.py` | ~100 |

### 5.4 Dead Code (LOW)

`storage/persistence.py`: Dead code already tracked in `dev/TODO.md`. Lines 73 and 187 contain unreachable paths.

### 5.5 Private API Access (LOW)

`gateway_routes.py:97`: Accesses `client._base_url` (private attribute) on the Anthropic client.

---

## 6. Test Coverage & Quality

### 6.1 Coverage Summary

- **87% overall coverage** (5,937 statements, 1,126 tests)
- All tests pass

### 6.2 Coverage Gaps

| Module | Issue |
|--------|-------|
| `streaming/queue_utils.py` | **Zero tests** — circuit breaker (safe_put) completely untested |
| `llm/types/` | **Zero tests** — Pydantic model validation untested |
| Concurrency | No tests for race conditions, queue overflow, or backpressure |
| `storage/persistence.py` | Background queue not tested under load |

### 6.3 Test Quality Issues

- **61 weak assertions**: Tests that assert truthiness (`assert result`) without checking specific values
- **No concurrency tests**: The streaming pipeline's TaskGroup, bounded queues, and circuit breaker are tested only in isolation, never under concurrent load
- **No property-based tests**: Complex streaming state machine would benefit from hypothesis testing

### 6.4 Test Infrastructure

- Unit tests block ALL network sockets via `conftest.py` monkeypatch (good)
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed (good)
- 3-second timeout on unit tests (good)
- E2E tests require full Docker stack (appropriate)

---

## 7. API Design & Contract Consistency

### 7.1 Headers Not Forwarded (HIGH)

Upstream API headers are silently dropped:
- `anthropic-version` — controls API behavior
- `anthropic-beta` — enables beta features (e.g., extended thinking)
- `openai-organization` — billing/access control

**Fix:** Forward recognized headers to upstream. At minimum, `anthropic-version` and `anthropic-beta`.

### 7.2 Error Format Mismatch (MEDIUM)

Auth and validation errors return FastAPI's default format:
```json
{"detail": "Authentication required"}
```

Instead of upstream API format:
```json
{"error": {"type": "authentication_error", "message": "..."}}
```

SDK clients (anthropic-python, openai-python) may not handle the FastAPI format correctly.

**Fix:** Return errors in the upstream API's format based on the request path (`/v1/messages` → Anthropic format, `/v1/chat/completions` → OpenAI format).

### 7.3 Anthropic Cache Tokens Dropped (MEDIUM)

**File:** `llm/anthropic_client.py:96-115`

`cache_creation_input_tokens` and `cache_read_input_tokens` from Anthropic responses are not forwarded to the client. Users cannot track prompt caching effectiveness.

**Fix:** Include cache token fields in the response usage object.

---

## 8. Configuration & Deployment

### 8.1 Build Tools in Production Image (MEDIUM)

**File:** `docker/Dockerfile.gateway:7-14`

Rust compiler, cargo, and build dependencies are included in the production Docker image. No multi-stage build. Estimated ~500MB+ waste.

**Fix:** Multi-stage Docker build — build stage with Rust/cargo, production stage with only Python runtime.

### 8.2 No Data Retention Policy (MEDIUM)

All database tables (`conversation_events`, `request_logs`, `policy_instances`) grow unboundedly. No TTL, no archival, no cleanup.

**Fix:** Add a retention policy — either a cron job or a background task that prunes old records.

### 8.3 No Startup Retry for External Services (MEDIUM)

The gateway crashes immediately if Postgres or Redis is temporarily unavailable at startup. No retry logic, no exponential backoff.

**Fix:** Add startup retry with exponential backoff for database and Redis connections.

### 8.4 Version Hardcoded (LOW)

**File:** `settings.py:52`

Version string is hardcoded rather than read from `pyproject.toml` or set via environment variable.

### 8.5 Unbounded Persistence Queue (LOW)

**File:** `storage/persistence.py:47`

The background persistence queue has no maximum size. Under sustained high throughput, this could consume unbounded memory.

---

## 9. OSS Best Practices Benchmark

Comparison against LiteLLM, Portkey, Kong AI Gateway, and FastAPI Guard:

### Where Luthien Leads

| Feature | Luthien | Peers |
|---------|---------|-------|
| Streaming pipeline | TaskGroup + bounded queues + safe_put circuit breaker | LiteLLM/Portkey use simpler unbounded streaming |
| Policy architecture | Dual interface (hook-based + execution-oriented) | Peers have simpler but less flexible policy systems |
| Observability integration | OpenTelemetry built-in from day one | Often added later or as plugins |

### Where Luthien Trails

| Feature | Status | Peers |
|---------|--------|-------|
| Rate limiting | Missing | All peers have it |
| Health checks | Basic `/health` | Peers check DB, Redis, upstream connectivity |
| Graceful shutdown | No drain timeout | LiteLLM, Kong handle in-flight requests |
| Upstream retry/fallback | Missing | LiteLLM has sophisticated retry + fallback |
| Circuit breaker (upstream) | Missing | Kong, Portkey have per-upstream circuit breakers |
| Key rotation | Missing | LiteLLM has DB-backed key rotation |
| Batch DB writes | Missing | Peers batch for throughput |
| Request/response validation | Partial | Peers validate against API schemas |

---

## 10. Observability & Production Monitoring

### 10.1 Current State

| Layer | Tool | Status | Assessment |
|-------|------|--------|------------|
| **Distributed tracing** | OTel SDK + Tempo | ✅ Fully implemented | Comprehensive span coverage across full request lifecycle (67 span attribute sets, 10+ files). Manual spans for pipeline phases, policy decisions, LLM calls, streaming. `restore_context()` for async generators. Strong. |
| **Structured logging** | stdlib `logging` + `SimpleJSONFormatter` | ⚠️ Basic | JSON logs to stdout with `trace_id`/`span_id` correlation. 37 modules use `logging.getLogger()`. No log aggregation (no Loki, no ELK). Sufficient for single-node, insufficient for multi-user. |
| **Event storage** | PostgreSQL + Redis pub/sub | ✅ Fully implemented | Full request/response payloads in `conversation_events`. Background queue for non-blocking writes. Real-time activity stream via Redis pub/sub. |
| **Monitoring UIs** | 6 custom HTML pages | ✅ Fully implemented | Activity monitor, diff viewer, request log browser, live conversation, history list/detail. Alpine.js frontend. |
| **Health checks** | `GET /health` | ❌ Shallow | Returns static `{"status": "healthy", "version": "2.0.0"}`. Checks nothing — not DB, not Redis, not upstream. Docker healthcheck calls this. |
| **Metrics** | 2 OTel counters | ❌ Effectively dead | `response.chunks.ingress` and `response.chunks.egress` in `transaction_recorder.py`. No metrics exporter configured — data goes nowhere. |
| **Error tracking** | None | ❌ Missing | No Sentry, Bugsnag, or Rollbar. Unhandled exceptions go to stdout only. Only `BackendAPIError` has a custom handler. |
| **Alerting** | None | ❌ Missing | No AlertManager, no PagerDuty, no Slack alerts. Errors pass silently. |
| **Continuous profiling** | None | ⏸️ Not needed yet | I/O-bound gateway — profiling becomes valuable only after known bottlenecks are fixed. |

### 10.2 Error Tracking — Sentry (HIGH, Must-Have for Demo)

**Gap:** The gateway has no way to detect, aggregate, or alert on its own failures. Unhandled exceptions go to stdout where they're invisible at scale. This is incompatible with a public demo where reliability must be demonstrable.

**Recommendation:** Adopt `sentry-sdk>=2.43.0`. Auto-detects FastAPI — no extras needed.

**Integration pattern:**
```python
import sentry_sdk

sentry_sdk.init(
    dsn="...",
    traces_sample_rate=0.0,  # CRITICAL: disable Sentry tracing — you have OTel+Tempo
    environment="production",
    release="luthien-proxy@2.0.0",
    send_default_pii=False,
)
```

**Critical gotchas:**
- **Disable Sentry tracing** (`traces_sample_rate=0.0`). Running Sentry tracing alongside OTel creates duplicate overhead and confusing data. Use Sentry for error tracking only.
- **Don't capture HTTPException** — FastAPI 4xx responses are not bugs. Default `failed_request_status_codes=[range(500, 599)]` is correct.
- **Performance at high RPS**: Known issue where Sentry can halve throughput when tracing is enabled. With tracing disabled, overhead is negligible.

**Free tier:** 5K errors/month, 1 user, 30 days retention. Team plan ($26/month) for multiple team members.

**Effort:** ~30 minutes. Directly addresses audit finding 3.2 (error detail leakage) — log to Sentry server-side instead of exposing to clients.

### 10.3 Metrics — Prometheus + Grafana (HIGH, Must-Have for Demo)

**Gap:** No request latency tracking, no error rates, no token throughput, no queue depth monitoring. The 2 existing OTel counters have no exporter. Every production LLM gateway (LiteLLM: 32+ metrics, Portkey, Bifrost) exposes a `/metrics` endpoint for Prometheus scraping. Not having this would be conspicuous.

**Recommendation:** Adopt `prometheus-fastapi-instrumentator>=7.0.0` for HTTP metrics + `prometheus-client>=0.21.0` for custom LLM metrics.

**Automatic HTTP metrics (out of the box):**
- `http_requests_total` (counter, by handler/status/method)
- `http_request_duration_seconds` (histogram, by handler/method)
- `http_request_size_bytes` / `http_response_size_bytes`

**Custom LLM-proxy metrics to add (based on LiteLLM's production metrics):**

| Metric | Type | Why It Matters |
|--------|------|----------------|
| `luthien_request_total_latency_seconds` | Histogram | End-to-end latency including policy execution |
| `luthien_upstream_latency_seconds` | Histogram | Upstream LLM provider latency (isolates your overhead) |
| `luthien_proxy_overhead_seconds` | Histogram | Your gateway's added latency (total − upstream) — the metric adopters care about most |
| `luthien_time_to_first_token_seconds` | Histogram | Critical for streaming UX |
| `luthien_tokens_total` | Counter (by model, direction) | Token throughput tracking |
| `luthien_request_errors_total` | Counter (by error type) | Error rate by provider/model |
| `luthien_policy_execution_seconds` | Histogram | Per-policy execution time |
| `luthien_streaming_queue_depth` | Gauge | Backpressure indicator |

**Grafana:** Add as Docker Compose service (observability profile). Use LiteLLM's [Grafana dashboard](https://grafana.com/grafana/dashboards/24055-litellm/) as template. Start with 1 dashboard, 4-6 panels: request rate, latency p50/p95/p99, error rate, token throughput.

**Effort:** ~2-4 hours for basic HTTP metrics + `/metrics` endpoint. ~1-2 days for full LLM-specific custom metrics with a Grafana dashboard.

### 10.4 Structured Logging — structlog (MEDIUM, Should-Have)

**Gap:** Current `SimpleJSONFormatter` works but is minimal. Third-party libraries (uvicorn, LiteLLM, asyncio) log in their own formats. No request-scoped context binding. No log aggregation service.

**Recommendation:** Adopt `structlog>=24.4.0`. It wraps stdlib `logging`, so existing `logging.getLogger()` calls continue working — migration is incremental.

**Why structlog over current setup:**
- Consistent JSON output for ALL loggers (including third-party)
- `contextvars`-based request context (perfect for async FastAPI — bind `request_id`, `model`, `user_id` once per request)
- Built-in OTel span context processor
- `orjson` backend for fast JSON serialization

**Migration path:** Non-breaking. Add structlog config in `create_app()`, new code uses `structlog.get_logger()`, existing `logging.getLogger()` calls get formatted through structlog's `ProcessorFormatter`.

**Effort:** ~1 day for initial setup. Migration of existing log calls is incremental and non-blocking.

### 10.5 Health Checks — Rich Checks (HIGH, Must-Have for Demo)

**Gap:** `GET /health` returns a static 200. Docker, Railway, and adopters' monitoring tools all depend on this endpoint to detect failures. A shallow health check means the gateway reports "healthy" even when DB is down, Redis is unreachable, or upstream APIs are broken.

**Recommendation:** Check DB connectivity, Redis connectivity, and report degraded state. Already tracked in `dev/TODO.md:128`.

```python
@app.get("/health")
async def health():
    db_ok = await check_db_connection(deps.db_pool)
    redis_ok = await check_redis_connection(deps.redis)
    status = "healthy" if (db_ok and redis_ok) else "degraded"
    return {
        "status": status,
        "version": settings.service_version,
        "checks": {"database": db_ok, "redis": redis_ok},
    }
```

Return HTTP 200 for healthy, HTTP 503 for degraded. Docker healthcheck will restart on repeated 503s.

**Effort:** ~1-2 hours.

### 10.6 Alerting (MEDIUM, Should-Have for Demo)

**Gap:** No alerting infrastructure. Errors, high latency, and service degradation pass silently.

**Recommendation:** Two layers:
1. **Sentry alerts** (free with Sentry): Slack/email on new error types, error regressions, spike detection
2. **Grafana alerting** (free with Grafana): Alert on high error rate (>5% of requests), high p95 latency (>10s), queue depth growing, health check failures

**Effort:** ~2-4 hours after Prometheus + Grafana are in place.

### 10.7 Continuous Profiling — Defer

**Not recommended for the 6-week timeline.** The gateway is I/O-bound (waiting on upstream LLMs), not CPU-bound. Profiling becomes valuable after:
1. Known bottlenecks from Section 4 are fixed
2. `luthien_proxy_overhead_seconds` metric shows unexpectedly high values
3. You need to optimize policy execution or streaming chunk assembly

When needed, use `pyinstrument` (supports `async_mode="enabled"`) as on-demand middleware, not continuous profiling.

### 10.8 Dependencies to Add

```toml
# pyproject.toml additions
dependencies = [
    "sentry-sdk>=2.43.0",                        # Error tracking (30 min)
    "prometheus-client>=0.21.0",                  # Custom metrics
    "prometheus-fastapi-instrumentator>=7.0.0",   # HTTP metrics auto-instrumentation
    "structlog>=24.4.0",                          # Structured logging (optional, can defer)
]
```

### 10.9 Six-Week Timeline to Demo

| Week | Deliverable | Effort |
|------|-------------|--------|
| 1 | Sentry integration + `prometheus-fastapi-instrumentator` + rich health check | 1 day |
| 2 | Custom LLM metrics (latency, tokens, errors, overhead) + Grafana dashboard | 1-2 days |
| 3 | structlog migration + OTel trace context in all logs | 1 day |
| 4 | Alerting rules (Grafana: high error rate, high latency; Sentry: new errors) | 0.5 day |
| 5-6 | Load test, tune alert thresholds, polish dashboards, fix Tier 1 audit items | ongoing |

---

## 11. Prioritized Improvement Roadmap

*Re-prioritized for April 16, 2026 demo deadline. Observability items promoted to Tier 1-2.*

### Tier 1 — Critical / Must-Have Before Demo (Weeks 1-2)

| # | Finding | Severity | Effort | Section |
|---|---------|----------|--------|---------|
| 1 | Cache AnthropicClient by credential hash | CRITICAL | Small | 4.1 |
| 2 | **Integrate Sentry (error-only, no tracing)** | **HIGH** | **30 min** | **10.2** |
| 3 | **Add Prometheus metrics + `/metrics` endpoint** | **HIGH** | **2-4 hrs** | **10.3** |
| 4 | **Rich health checks (DB, Redis connectivity)** | **HIGH** | **1-2 hrs** | **10.5** |
| 5 | Apply `sanitize_headers()` in DebugLoggingPolicy | HIGH | Trivial | 3.1 |
| 6 | Replace raw exception messages with generic errors | HIGH | Small | 3.2 |
| 7 | Detect client disconnect in streaming | HIGH | Small | 4.2 |
| 8 | Forward `anthropic-version` and `anthropic-beta` headers | HIGH | Small | 7.1 |

### Tier 2 — High Impact / Should-Have Before Demo (Weeks 3-4)

| # | Finding | Severity | Effort | Section |
|---|---------|----------|--------|---------|
| 9 | **Custom LLM metrics (latency, tokens, overhead) + Grafana dashboard** | **HIGH** | **1-2 days** | **10.3** |
| 10 | **Adopt structlog for structured logging** | **MEDIUM** | **1 day** | **10.4** |
| 11 | **Alerting rules (Grafana + Sentry → Slack)** | **MEDIUM** | **2-4 hrs** | **10.6** |
| 12 | Add per-key rate limiting on `/v1/` routes | HIGH | Medium | 3.4 |
| 13 | Extract shared judge logic from duplicated policies | HIGH | Medium | 5.1 |
| 14 | Return errors in upstream API format | MEDIUM | Medium | 7.2 |
| 15 | Forward Anthropic cache usage tokens | MEDIUM | Small | 7.3 |
| 16 | Pipeline Redis operations | MEDIUM | Small | 4.4 |
| 17 | Allowlist-validate policy class refs before `__import__()` | MEDIUM | Small | 3.3 |
| 18 | Replace `eval()` with `ast.literal_eval()` | MEDIUM | Trivial | 3.3 |
| 19 | Multi-stage Docker build | MEDIUM | Small | 8.1 |

### Tier 3 — Medium Priority (Weeks 5-6 / Post-Demo Backlog)

| # | Finding | Severity | Effort | Section |
|---|---------|----------|--------|---------|
| 20 | Add tests for `queue_utils.py` | MEDIUM | Small | 6.2 |
| 21 | Add concurrency tests for streaming pipeline | MEDIUM | Medium | 6.2 |
| 22 | Reduce triple chunk buffering | MEDIUM | Small | 4.3 |
| 23 | Add data retention policy / cleanup job | MEDIUM | Medium | 8.2 |
| 24 | Add startup retry with backoff | MEDIUM | Small | 8.3 |
| 25 | Add graceful shutdown with drain timeout | MEDIUM | Medium | 9 |
| 26 | Remove 3 bare `except: pass` in policy_discovery | MEDIUM | Trivial | 3.5 |
| 27 | Fix HTTP 200 + `success: false` in admin routes | MEDIUM | Small | 3.5 |
| 28 | Add Pydantic model tests for `llm/types/` | MEDIUM | Small | 6.2 |
| 29 | Consolidate LiteLLM type imports via re-export | MEDIUM | Medium | 2.2 |
| 30 | Bound persistence queue size | LOW | Trivial | 8.5 |
| 31 | Remove dead code in `storage/persistence.py` | LOW | Trivial | 5.4 |
| 32 | Stop accessing `client._base_url` | LOW | Trivial | 5.5 |

### Tier 4 — Strategic / Long-Term (Post-Demo)

| # | Finding | Severity | Effort | Section |
|---|---------|----------|--------|---------|
| 33 | Upstream retry + fallback logic | — | Large | 9 |
| 34 | Per-upstream circuit breaker | — | Large | 9 |
| 35 | DB-backed API key rotation | — | Large | 9 |
| 36 | Batch DB writes for throughput | — | Medium | 9 |
| 37 | Request/response schema validation | — | Medium | 9 |
| 38 | Reduce `type: ignore` count (currently 29) | — | Medium | 5.2 |
| 39 | Consider Pyright strict mode | — | Large | 5.2 |
| 40 | Unify OpenAI/Anthropic policy interface | — | Very Large | 2.3 |
| 41 | Continuous profiling (Pyroscope/pyinstrument) | — | Medium | 10.7 |

---

## 12. GitHub Issue/PR Cross-Reference

*Cross-referenced on 2026-03-05 against 7 open issues, 13 open PRs, and 2 relevant closed/merged PRs.*

### 12.1 Audit Findings With Existing PRs/Issues

| Roadmap # | Finding | Section | PR/Issue | Status | Notes |
|-----------|---------|---------|----------|--------|-------|
| **#8** | Headers not forwarded | 7.1 | PR #269 (MERGED) + PR #273 (OPEN) | **FULLY ADDRESSED** | PR #269 forwarded `anthropic-beta` only. PR #273 forwards all `anthropic-*` headers + `user-agent`, adds `backend_headers` to policy IO protocol. Merge #273 to close this finding. |
| **#17/#18** | eval()/__import__() security | 3.3 | PR #232 (OPEN, docs) | **DOCUMENTED, NOT FIXED** | PR #232 adds security documentation explaining the trust boundaries. Does not implement the allowlist validation the audit recommends. PR #252 (Dynamic Policy Creation) adds *more* dynamic loading with safety validation — makes this finding more important, not less. |
| **#12** | No rate limiting | 3.4 | PR #234 (CLOSED) | **NOT ADDRESSED** | PR #234 added message count validation (MAX_MESSAGE_COUNT=10,000) — this is input validation, not rate limiting. The audit finding about per-key request rate limiting remains open. PR was closed, not merged. |
| **#13** | Policy code duplication (~300 lines) | 5.1 | PR #243 (OPEN) | **WORSENED** | PR #243 (DogfoodSafetyPolicy) explicitly "follows the ToolCallJudgePolicy pattern" — this is the source of the duplication the audit identified. Merging #243 without extracting shared logic will cement the duplication. |

### 12.2 Audit Findings With NO GitHub Tracking

These 37 roadmap items have no corresponding GitHub issue or PR. Items marked ⚡ are Tier 1 (must-have before demo).

| Roadmap # | Finding | Severity | Section |
|-----------|---------|----------|---------|
| ⚡ **#1** | Cache AnthropicClient by credential hash | CRITICAL | 4.1 |
| ⚡ **#2** | Integrate Sentry (error-only, no tracing) | HIGH | 10.2 |
| ⚡ **#3** | Add Prometheus metrics + `/metrics` endpoint | HIGH | 10.3 |
| ⚡ **#4** | Rich health checks (DB, Redis connectivity) | HIGH | 10.5 |
| ⚡ **#5** | Apply `sanitize_headers()` in DebugLoggingPolicy | HIGH | 3.1 |
| ⚡ **#6** | Replace raw exception messages with generic errors | HIGH | 3.2 |
| ⚡ **#7** | Detect client disconnect in streaming | HIGH | 4.2 |
| **#9** | Custom LLM metrics + Grafana dashboard | HIGH | 10.3 |
| **#10** | Adopt structlog for structured logging | MEDIUM | 10.4 |
| **#11** | Alerting rules (Grafana + Sentry → Slack) | MEDIUM | 10.6 |
| **#14** | Return errors in upstream API format | MEDIUM | 7.2 |
| **#15** | Forward Anthropic cache usage tokens | MEDIUM | 7.3 |
| **#16** | Pipeline Redis operations | MEDIUM | 4.4 |
| **#19** | Multi-stage Docker build | MEDIUM | 8.1 |
| **#20-#21** | Tests for queue_utils.py + concurrency tests | MEDIUM | 6.2 |
| **#22** | Reduce triple chunk buffering | MEDIUM | 4.3 |
| **#23** | Data retention policy / cleanup job | MEDIUM | 8.2 |
| **#24** | Startup retry with backoff | MEDIUM | 8.3 |
| **#25** | Graceful shutdown with drain timeout | MEDIUM | 9 |
| **#26-#27** | Remove bare except:pass + fix HTTP 200+success:false | MEDIUM | 3.5 |
| **#28** | Pydantic model tests for llm/types/ | MEDIUM | 6.2 |
| **#29** | Consolidate LiteLLM type imports | MEDIUM | 2.2 |
| **#30-#32** | Bound persistence queue, dead code, private API access | LOW | 5.4/5.5/8.5 |
| **#33-#41** | Strategic items (retry, circuit breaker, key rotation, etc.) | — | 9/10.7 |

### 12.3 Blind Spots — GitHub Items NOT in Audit

These issues and PRs cover problems the audit did not identify. Items marked 🔴 are demo-critical.

#### 12.3.1 Functional Bugs

| PR/Issue | Title | Impact | Demo Risk |
|----------|-------|--------|-----------|
| 🔴 **PR #242** | QA trial: Activity Monitor completely broken | `emitter.py` silently fails — events never reach Redis pub/sub. The Activity Monitor (`/activity/monitor`) shows nothing. | **CRITICAL for demo** — this is a primary demo feature |
| 🔴 **PR #268** | Anthropic streaming history not recorded | Streaming Anthropic responses were not stored in conversation history. Conversation history page shows incomplete data. | **HIGH for demo** — history page is a demo feature |
| **PR #242** | QA trial: 8 additional bugs | AUTH_MODE DB override, Python 3.13+ not checked at install, other onboarding issues | Medium — affects new user experience |

#### 12.3.2 Demo-Critical UI/UX Gaps

| PR/Issue | Title | Impact | Demo Risk |
|----------|-------|--------|-----------|
| 🔴 **Issue #137** | Policy Config UI: no call_id link to Diff Viewer | After testing a policy, users can't navigate to the diff view. Demo flow is broken. | **HIGH for demo** — breaks the "test → see diff" story |
| 🔴 **Issue #136** | Policy Config UI: judge results not surfaced | Users can't see WHY a policy allowed/blocked content. Judge probability, explanation, and decision are recorded but not displayed. | **HIGH for demo** — "why was this blocked?" is the first question |
| **PR #272** | Form renderer UI improvements | `list[list[str]]` config fields render incorrectly, static files cached aggressively | Medium — affects policy config UX |

#### 12.3.3 Feature Gaps & Strategic Items

| PR/Issue | Title | Impact |
|----------|-------|--------|
| **PR #263** | Luthien CLI tool | New `luthien` CLI for managing gateways (`luthien status`, `luthien claude`, `luthien up/down`). Adoption enabler. |
| **PR #270** | Claude Pro/Max OAuth quickstart | README only covers API key auth. Pro/Max users (no API key) have no onboarding path. Adoption barrier. |
| **PR #271** | Testing without upstream API key | Policy test UI requires real API credentials. Blocks evaluation by users without keys. |
| **PR #266** | Overseer multi-turn e2e test harness | Automated long-running test sessions to find gateway bugs. New testing approach. |
| **PR #264** | No Silent Failures default policy | Config-only: activates ToolCallJudgePolicy to catch silent error swallowing. |
| **PR #252** | Dynamic Policy Creation System (WIP) | LLM-powered policy generation from natural language. Major feature — introduces new security surface (see 12.1, finding #17/#18). |
| **Issue #180** | Codex compatibility (4 parked PRs) | Codex support has known bugs. Parked pending streaming refactor. |
| **Issue #124** | conversation_transcript SQL view | Human-readable conversation logs. Useful for debugging and export. |
| **Issue #42** | METR-Style Benchmark proposal | Rigorous safety evaluation framework. Strategic for credibility. |
| **Issue #41** | Petri Integration proposal | Anthropic's auditing framework for automated red-teaming. |
| **Issue #127** | claude-code-action pinned to v1.0.0 | CI/CD: newer versions fail. Blocks GitHub Actions improvements. |
| **PR #233** | Add shellcheck linting | Shell script quality — prevents bash compatibility issues (COE from PR #202). |

### 12.4 Revised Demo Priority — Combined View

Merging audit findings with GitHub blind spots, the demo-critical items are:

| Priority | Item | Source | Status |
|----------|------|--------|--------|
| **P0** | Fix Activity Monitor (emitter.py → Redis pub/sub) | PR #242 (blind spot) | PR open |
| **P0** | Cache AnthropicClient by credential hash | Audit #1 (CRITICAL) | No PR |
| **P0** | Integrate Sentry | Audit #2 | No PR |
| **P0** | Add Prometheus + `/metrics` | Audit #3 | No PR |
| **P0** | Rich health checks | Audit #4 | No PR |
| **P1** | Record Anthropic streaming history | PR #268 (blind spot) | PR open |
| **P1** | Forward all anthropic-* headers | Audit #8 / PR #273 | PR open — merge |
| **P1** | Surface judge results in Policy Config UI | Issue #136 (blind spot) | Issue open |
| **P1** | Add call_id link in Policy Config UI | Issue #137 (blind spot) | Issue open |
| **P1** | Sanitize headers in DebugLoggingPolicy | Audit #5 | No PR |
| **P1** | Replace raw exception messages | Audit #6 | No PR |
| **P1** | Detect client disconnect in streaming | Audit #7 | No PR |
| **P2** | Custom LLM metrics + Grafana dashboard | Audit #9 | No PR |
| **P2** | Claude Pro/Max OAuth docs | PR #270 (blind spot) | PR open |
| **P2** | Per-key rate limiting | Audit #12 | No PR |
| **P2** | Extract shared judge logic (before merging #243) | Audit #13 | No PR |

---

## 13. Trello Cross-Reference (Tier 1–2 Findings)

*Completed: 2026-03-07. All Tier 1–2 audit findings mapped against Trello board "Luthien" (ID: 67cf59bdf2e5e435dcfc5690). Untracked findings verified in codebase and added as cards.*

### 13.1 Cross-Reference Table

| Audit # | Finding | Tier | Trello Status | Trello Card ID |
|---------|---------|------|---------------|----------------|
| **#1** | Cache AnthropicClient (CRITICAL) | 1 | **CREATED** — verified at gateway_routes.py:106,113,114 | `69ac0a1838c550871d842c39` |
| **#2** | Integrate Sentry | 1 | **CREATED** — no code to verify (new feature) | `69ac0a577f14a0e7e059719b` |
| **#3** | Add Prometheus metrics | 1 | **CREATED** — no code to verify (new feature) | `69ac0a5e79a4856266be6f6d` |
| **#4** | Rich health checks | 2 | **ALREADY TRACKED** — "Add degraded state reporting to /health endpoint" (from TODO.md import) | existing card |
| **#5** | Sanitize headers in DebugLoggingPolicy | 1 | **CREATED** — verified at debug_logging_policy.py:64,72 | `69ac0a1ed316a73e8ffcf027` |
| **#6** | Error detail leakage (11 instances) | 1 | **CREATED** — verified across 5 files | `69ac0a2574e700a780f8e6b4` |
| **#7** | Client disconnect detection | 1 | **CREATED** — no code to verify (missing feature) | `69ac0a6494c707a956c847f7` |
| **#8** | Forward headers upstream | 1 | **ALREADY ADDRESSED** — PR #273 merged | N/A |
| **#9** | Custom LLM metrics + Grafana | 2 | **CREATED** — no code to verify (new feature) | `69ac0a6b90c78d1d56c8cb9d` |
| **#10** | Adopt structlog | 2 | **CREATED** — no code to verify (new feature) | `69ac0a71454229ac93c6e173` |
| **#11** | Alerting rules | 2 | **CREATED** — no code to verify (new feature) | `69ac0a7828ab851ee8cf4441` |
| **#12** | Rate limiting | 2 | **ALREADY TRACKED** — "Add rate limiting middleware" (Medium Priority) | existing card |
| **#13** | Extract shared judge logic | 2 | **CREATED** — duplication confirmed in tool_call_judge + dogfood_safety | `69ac0a80356970d9f9a8a601` |
| **#14** | Error format mismatch (HTTPException) | 2 | **CREATED** — verified: no HTTPException handler in main.py | `69ac0a2c92dd50e10dbca106` |
| **#15** | Anthropic cache tokens not tracked | 2 | **CREATED** — verified: zero references in codebase | `69ac0a33e3d04424aad5999a` |
| **#16** | Pipeline Redis operations (4 round-trips) | 2 | **CREATED** — verified at credential_manager.py:296-315 | `69ac0a39804089308221dd5f` |
| **#17** | Allowlist __import__() | 2 | **PARTIALLY TRACKED** — "Add security documentation for dynamic policy loading" exists | existing card |
| **#18** | Replace eval() | 2 | **CREATED** — verified at policy_discovery.py:134 | `69ac0a3f278a2a397d77ca6e` |
| **#19** | Multi-stage Docker build | 2 | **CREATED** — verified at Dockerfile.gateway:7-14 | `69ac0a455551caf404ba57c2` |

### 13.2 Summary

- **19 Tier 1–2 findings** total
- **15 Trello cards created** (new)
- **3 already tracked** in Trello (#4, #8, #12 + partial #17)
- **1 already addressed** by merged PR (#8)
- **8 findings verified** as still present in codebase before card creation
- All new cards placed in "Next Up" list with appropriate Bug/Priority/Category labels

---

## Appendix: Codebase Statistics

| Metric | Value |
|--------|-------|
| Source files | 93 |
| Total lines | 16,317 |
| Functions | 619 |
| Classes | 146 |
| Test files | ~80 |
| Test count | 1,126 |
| Test coverage | 87% (5,937 statements) |
| Pyright errors | 0 |
| Pyright warnings | 0 |
| `type: ignore` | 29 |
| `noqa` suppressions | 26 |
| `Any` usages | 138 |
| `cast()` usages | 73 |
| Functions >40 lines | 25 |
| Largest function | 191 lines |
