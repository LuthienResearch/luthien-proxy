# OpenTelemetry Migration Progress

**Branch:** `integrated-architecture`
**Started:** 2025-10-18
**Status:** IN PROGRESS - Phase 10 (9 of 11 phases complete - 82%)

---

## Goal
Replace custom event system (ActivityEvent, PolicyEvent) with OpenTelemetry for distributed tracing while keeping Redis pub/sub for real-time UI.

---

## Completed Phases

### ✅ Phase 1: Observability Infrastructure (DONE)
**Commit:** `f21ad91` - "chore: add observability infrastructure (Grafana/Loki/Tempo)"
**Commit:** `4b41d37` - "fix: update observability stack configuration"

**What was done:**
- Created `observability/` directory with configs for Tempo, Loki, Grafana
- Added docker-compose services with `profiles: ["observability"]`
- Created helper script: `scripts/observability.sh`
- Configured 24h retention with aggressive compaction
- Auto-configured Grafana datasources for log ↔ trace correlation
- Fixed Loki config (added `delete_request_store: filesystem`)
- Removed health checks (minimal images lack nc/wget)

**Verified working:**
```bash
./scripts/observability.sh up -d
./scripts/observability.sh status
# All three services running: tempo, loki, grafana
# Grafana accessible at http://localhost:3000
```

### ✅ Phase 2: OpenTelemetry Dependencies (DONE)
**Commit:** `0161741` - "chore: add OpenTelemetry dependencies"

**What was done:**
- Added to `pyproject.toml`:
  - opentelemetry-api>=1.20.0
  - opentelemetry-sdk>=1.20.0
  - opentelemetry-exporter-otlp-proto-grpc>=1.20.0
  - opentelemetry-instrumentation-fastapi>=0.41b0
  - opentelemetry-instrumentation-redis>=0.41b0
- Ran `uv sync` - all dependencies installed successfully

**Verified working:**
```bash
uv sync  # Installed 17 new packages
python -c "import opentelemetry; print('OK')"  # Imports work
```

---

## Completed Phases (continued)

### ✅ Phase 3: Telemetry Module & Event Bridge (DONE)

**Commit:** 992d35c - "feat: add OpenTelemetry telemetry module and event bridge"

**What was done:**
- Created `src/luthien_proxy/v2/telemetry.py`:
  - `configure_tracing()` - Sets up OTel SDK, OTLP exporter to Tempo
  - `configure_logging()` - Custom TraceContextFormatter adds trace_id/span_id to logs
  - `instrument_app(app)` - Auto-instruments FastAPI with OTel
  - `instrument_redis()` - Auto-instruments Redis with OTel
  - `setup_telemetry(app)` - Main entry point for all telemetry setup
  - Exports `tracer` for manual instrumentation
  - Environment variables: OTEL_ENABLED, OTEL_ENDPOINT, SERVICE_NAME, SERVICE_VERSION, ENVIRONMENT

- Created `src/luthien_proxy/v2/observability/` package:
  - `__init__.py` - Package initialization, exports SimpleEventPublisher
  - `bridge.py` - SimpleEventPublisher for Redis pub/sub to keep real-time UI working
  - Publishes to "luthien:activity" channel with call_id, event_type, timestamp, data

**Verified working:**
```bash
# Import tests
uv run python -c "from luthien_proxy.v2 import telemetry; print('OK')"
uv run python -c "from luthien_proxy.v2.observability import SimpleEventPublisher; print('OK')"

# Function tests
uv run python -c "
import os
os.environ['OTEL_ENABLED'] = 'false'
from luthien_proxy.v2.telemetry import setup_telemetry
tracer = setup_telemetry()
print('Telemetry setup OK')
"
# All tests passed
```

**Environment variables to add** (`.env.example`):
```bash
OTEL_ENABLED=true
OTEL_ENDPOINT=http://tempo:4317  # Docker service name
SERVICE_NAME=luthien-proxy-v2
SERVICE_VERSION=2.0.0
ENVIRONMENT=development
```

---

## Completed Phases (continued)

### ✅ Phase 4: Update PolicyContext & NoOpPolicy (DONE)

**Commit:** b63d329 - "refactor: migrate PolicyContext to OpenTelemetry spans"

**What was done:**
- Updated `src/luthien_proxy/v2/policies/context.py`:
  - **BREAKING:** Changed constructor from `emit_event: Callable` to `span: Span` + optional `event_publisher`
  - `emit()` now adds OTel span events instead of creating PolicyEvent objects
  - Optionally publishes to Redis via SimpleEventPublisher for real-time UI
  - Span events include all details as attributes
  - Fire-and-forget Redis publishing (no blocking)

- Updated `tests/unit_tests/v2/test_policies.py`:
  - Updated `make_context()` helper to create mock span
  - All NoOpPolicy tests pass (NoOpPolicy doesn't call emit())

**Verified working:**
```bash
uv run pytest tests/unit_tests/v2/test_policies.py -v
# All 6 tests passed
```

**Known issues:**
- tests/unit_tests/v2/test_control_local.py fails (expected - needs Phase 5 updates)
- NoOpPolicy doesn't change (doesn't call emit())
- PolicyEvent model still exists (will remove in Phase 8)

### ✅ Phase 5: Update ControlPlaneLocal (DONE)

**Commit:** 502ec3a - "refactor: migrate ControlPlaneLocal to OpenTelemetry"

**What was done:**
- Updated `src/luthien_proxy/v2/control/local.py`:
  - **BREAKING:** Changed constructor from `redis_client: Redis | None` to `event_publisher: SimpleEventPublisher | None`
  - Removed ActivityPublisher dependency
  - Added OTel span creation for all three methods:
    - `process_request` → "control_plane.process_request" span
    - `process_full_response` → "control_plane.process_full_response" span
    - `process_streaming_response` → "control_plane.process_streaming_response" span
  - Each span includes luthien.* attributes (call_id, model, stream.enabled, stream.chunk_count, policy.success, etc.)
  - Errors recorded as span events instead of PolicyEvents
  - Passes span to PolicyContext instead of emit_event callback

- Updated `tests/unit_tests/v2/test_control_local.py`:
  - Fixed control_plane fixture to match new signature
  - Removed entire TestControlPlaneLocalEvents class (91 lines deleted)
  - Removed all event assertion checks from remaining tests
  - Tests now verify behavior, not internal event collection
  - All 12 remaining tests pass

**Verified working:**

```bash
uv run pytest tests/unit_tests/v2/test_control_local.py -v
# All 12 tests passed
```

**Breaking change details:**
- OLD: `ControlPlaneLocal(policy, redis_client: Redis | None)`
- NEW: `ControlPlaneLocal(policy, event_publisher: SimpleEventPublisher | None)`

### ✅ Phase 6: Update main.py Gateway (DONE)

**Commit:** c2b0ea9 - "refactor: integrate OpenTelemetry into main.py gateway"

**What was done:**
- Updated `src/luthien_proxy/v2/main.py`:
  - Added `setup_telemetry(app)` to lifespan
  - Replaced ActivityPublisher with SimpleEventPublisher
  - Added gateway-level span for /v1/chat/completions endpoint
  - Set span attributes: luthien.call_id, luthien.endpoint, luthien.model, luthien.stream
  - Replaced activity_publisher.publish() calls with event_publisher.publish_event()
  - Simplified event payloads for real-time UI

**Verified working:**
```bash
uv run pytest tests/unit_tests/v2/ -v
# All tests passing
```

### ✅ Phase 7: Update StreamingOrchestrator (DONE)

**Commit:** e9b7ded - "feat: add OpenTelemetry tracing to StreamingOrchestrator"

**What was done:**
- Updated `src/luthien_proxy/v2/control/streaming.py`:
  - Added optional `span` parameter to `process()` method
  - Span events: orchestrator.start, orchestrator.complete, orchestrator.error
  - Span attributes: timeout_seconds, chunk_count, success
  - Errors recorded as span events with attributes

- Updated `src/luthien_proxy/v2/control/local.py`:
  - Pass span to orchestrator.process()

**Verified working:**
```bash
uv run pytest tests/unit_tests/v2/test_control_local.py -v
# All 12 tests passing including streaming tests
```

### ✅ Phase 8: Remove Old Event System (DONE)

**Commit:** 22fb10a - "refactor: remove old event system (ActivityPublisher, PolicyEvent)"

**What was done:**
- **DELETED FILES:**
  - `src/luthien_proxy/v2/activity/events.py` (ActivityEvent classes)
  - `src/luthien_proxy/v2/activity/publisher.py` (ActivityPublisher)

- **Updated files:**
  - `src/luthien_proxy/v2/control/models.py` - Removed PolicyEvent class (25 lines deleted)
  - `src/luthien_proxy/v2/control/__init__.py` - Removed PolicyEvent from exports
  - `src/luthien_proxy/v2/main.py` - Removed all ActivityPublisher imports and usage
  - `tests/unit_tests/v2/test_control_models.py` - Removed 7 PolicyEvent tests

**Migration impact:**
- Removed ~150 lines of old event code
- Simplified event publishing with lightweight JSON events
- All observability flows through OpenTelemetry + SimpleEventPublisher

**Verified working:**
```bash
uv run pytest tests/unit_tests/v2/
# 49 tests passing (down from 54 - removed 5 PolicyEvent tests)
```

### ✅ Phase 9: Final Test Validation (DONE)

**Commit:** 244a78e - "refactor: complete Phase 9 - fix type errors and import issues"

**What was done:**
- Fixed import errors after deleting files:
  - Updated `src/luthien_proxy/v2/activity/__init__.py` - Removed deleted module imports
  - Moved `V2_ACTIVITY_CHANNEL` constant to `stream.py`
  - Removed PolicyEvent from `control/interface.py` and deleted `get_events()` method

- Fixed pyright type errors:
  - Added type ignores for OTel attribute assignments in `policies/context.py`
  - Added type ignores for dict assignments in `observability/bridge.py`

**Verified working:**
```bash
./scripts/dev_checks.sh
# ✅ All checks passed: ruff format, ruff lint, pyright, 49 tests passing
# ✅ 100% coverage on streaming.py and models.py
# ✅ 61% overall coverage
```

---

## Current Phase

### 🔄 Phase 10: Documentation & Dashboard (IN PROGRESS)

**Next steps:**
1. Create `dev/context/observability-guide.md`
2. Create `dev/context/otel-conventions.md`
3. Create basic Grafana dashboard JSON
4. Update main README with observability section

---

## Upcoming Phases

### Phase 11: Final Validation
- Start observability stack
- Run E2E tests
- Verify traces in Grafana/Tempo
- Verify logs in Loki
- Test real-time UI at /v2/activity/monitor

---

## Important Design Decisions

1. **Real-time UI:** Keeping `/v2/activity/monitor` working with simplified events via Redis bridge
2. **Breaking changes:** Acceptable - only NoOpPolicy affected currently
3. **Test deletion:** Delete ~15 event-checking tests (test behavior, not implementation)
4. **Implementation:** Incremental with small commits per phase
5. **Branch:** Work on `integrated-architecture` (already a feature branch, no PR needed)

---

## OTel Conventions (for reference)

**Standard attributes:**
- `luthien.call_id` - Unique request identifier
- `luthien.policy.name` - Policy class name
- `luthien.model` - LLM model
- `luthien.stream.enabled` - Is streaming
- `luthien.tokens.total` - Token count

**Span naming:**
- `control_plane.process_request`
- `control_plane.process_full_response`
- `control_plane.process_streaming_response`
- `streaming.orchestrate`
- `llm.completion`
- `gateway.request_received`

**Span events (from PolicyContext.emit):**
- `policy.content_filtered`
- `policy.request_modified`
- etc.

---

## Testing Strategy

**After each phase:**
1. Verify imports work
2. Run relevant tests
3. Check no regressions
4. Commit with descriptive message

**Final validation:**
```bash
# Start observability stack
./scripts/observability.sh up -d

# Start app
docker compose up -d

# Make test request
./scripts/test_v2_proxy.py

# Check Grafana
open http://localhost:3000
# Should see: traces in Tempo, logs in Loki, correlation working
```

---

## Recovery Commands

If something breaks:

```bash
# Rollback to last commit
git reset --hard HEAD~1

# Disable OTel
export OTEL_ENABLED=false

# Stop observability stack
./scripts/observability.sh down

# Clean observability data
./scripts/observability.sh clean
```

---

## Current Git Status

```
On branch: integrated-architecture
Last commit: 502ec3a - refactor: migrate ControlPlaneLocal to OpenTelemetry
Commits ahead of origin: 6
```

---

## Files Modified in Migration

**New files:**

- `observability/` - Full observability stack config (Tempo, Loki, Grafana)
- `scripts/observability.sh` - Helper script for stack management
- `src/luthien_proxy/v2/telemetry.py` - OTel configuration and setup
- `src/luthien_proxy/v2/observability/__init__.py` - Observability package
- `src/luthien_proxy/v2/observability/bridge.py` - Redis pub/sub bridge for UI
- `dev/OTEL_MIGRATION_PROGRESS.md` - This file

**Modified files:**

- `docker-compose.yaml` - Added observability services with profiles
- `.gitignore` - Added observability/data/
- `pyproject.toml` - Added OTel dependencies
- `uv.lock` - Updated with new dependencies
- `src/luthien_proxy/v2/policies/context.py` - OTel span integration
- `src/luthien_proxy/v2/control/local.py` - OTel span creation, removed events
- `tests/unit_tests/v2/test_policies.py` - Updated for new PolicyContext
- `tests/unit_tests/v2/test_control_local.py` - Removed event tests (91 lines deleted)

**To be deleted (Phase 8):**

- `src/luthien_proxy/v2/activity/events.py`
- `src/luthien_proxy/v2/activity/publisher.py`
- PolicyEvent from `src/luthien_proxy/v2/control/models.py`

---

**Last updated:** 2025-10-18 (Phase 5 complete, starting Phase 6)
