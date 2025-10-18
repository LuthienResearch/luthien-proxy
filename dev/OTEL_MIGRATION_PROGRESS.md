# OpenTelemetry Migration Progress

**Branch:** `integrated-architecture`
**Started:** 2025-10-18
**Status:** IN PROGRESS - Phase 3

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

**Commit:** (pending) - "feat: add OpenTelemetry telemetry module and event bridge"

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

## Current Phase

### 🔄 Phase 4: Update PolicyContext & NoOpPolicy (NEXT)

**Breaking change:** PolicyContext.emit() will add span events instead of collecting events

**Next steps:**
1. Update `src/luthien_proxy/v2/policies/base.py`:
   - Remove event collection from PolicyContext
   - Add span event emission in emit()
   - Update type hints and docstrings

2. Update `src/luthien_proxy/v2/policies/noop.py`:
   - Update to use new PolicyContext signature
   - Test that NoOpPolicy still works

---

## Upcoming Phases

### Phase 4: Update PolicyContext & NoOpPolicy (BREAKING)
- **Breaking change:** New PolicyContext signature
- Update `emit()` to add OTel span events
- Only affects NoOpPolicy currently (easy to update)

### Phase 5: Update ControlPlaneLocal
- Remove: `_events` dict, `get_events()`, `ActivityPublisher`
- Add: OTel span creation for each method
- Simplify to pure policy execution

### Phase 6: Update main.py Gateway
- Initialize telemetry in lifespan
- Remove manual ActivityPublisher calls
- Add spans for gateway operations & LLM calls

### Phase 7: Update StreamingOrchestrator
- Add optional span creation
- Already extracted to `streaming.py` (clean!)

### Phase 8: Remove Old Event System
**Files to DELETE:**
- `src/luthien_proxy/v2/activity/events.py`
- `src/luthien_proxy/v2/activity/publisher.py`
- `src/luthien_proxy/v2/control/models.py` (PolicyEvent)

**Files to UPDATE:**
- `src/luthien_proxy/v2/activity/__init__.py` - Remove exports
- `src/luthien_proxy/v2/control/__init__.py` - Remove PolicyEvent
- `src/luthien_proxy/v2/control/interface.py` - Remove get_events()

### Phase 9: Update Tests
- Remove ~15 tests using `get_events()`
- Update PolicyContext usage
- Add span assertion tests

### Phase 10: Documentation & Dashboard
- Create `dev/context/observability-guide.md`
- Create `dev/context/otel-conventions.md`
- Update `dev/NOTES.md`, `dev/TODO.md`
- Create basic Grafana dashboard

### Phase 11: Final Validation
- Run `./scripts/dev_checks.sh`
- Manual testing
- E2E testing

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
Last commit: 4b41d37 - fix: update observability stack configuration
Next commit will be: feat: add OpenTelemetry telemetry module
```

---

## Files Modified So Far

```
observability/README.md                     (new)
observability/.gitignore                    (new)
observability/tempo/tempo.yaml              (new)
observability/loki/loki.yaml                (new, fixed)
observability/grafana/datasources.yaml      (new)
observability/grafana/dashboards/           (new)
scripts/observability.sh                    (new)
docker-compose.yaml                         (modified)
.gitignore                                  (modified)
pyproject.toml                              (modified)
```

---

## Next Actions (Phase 3)

1. Create telemetry.py module
2. Test imports and basic functionality
3. Create observability/bridge.py
4. Test that telemetry setup works
5. Commit and move to Phase 4

---

**Last updated:** 2025-10-18 (during Phase 3)
