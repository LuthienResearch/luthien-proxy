# Development Guide

Development reference for contributing to Luthien. For user-facing docs, see **[README.md](README.md)**.

## Development Commands

```bash
# Start the gateway locally (default — no Docker needed)
./scripts/start_gateway.sh

# Or with Docker Compose (multi-user/production):
# docker compose restart gateway

# Run unit tests
uv run pytest tests/luthien_proxy/unit_tests

# Run integration tests
uv run pytest tests/luthien_proxy/integration_tests

# Run e2e tests (slow, use sparingly)
uv run pytest -m e2e

# Test the gateway
./scripts/test_gateway.sh

# Format and lint
./scripts/format_all.sh

# Full dev checks (format + lint + tests + type check)
./scripts/dev_checks.sh

# Type check only
uv run pyright
```

## Dev Tooling

- Lint/format: `uv run ruff check` and `uv run ruff format`. Core rules enabled (E/F/I/D). Line length is 120; long-line lint (E501) is ignored to avoid churn after formatting.

Editor setup (VS Code)
- Install the Ruff extension.
- In this repo, VS Code uses Ruff for both formatting and import organization via `.vscode/settings.json`.
- Type checking: `uv run pyright` (configured in `[tool.pyright]` within `pyproject.toml`).
- Tests: `uv run pytest -q` with coverage for `src/luthien_proxy/**` configured in `[tool.pytest.ini_options]`.
- Config consolidation: Ruff, Pytest, and Pyright live in `pyproject.toml` to avoid extra files.

## Architecture

The gateway integrates everything into a single FastAPI application:

- **Gateway** (`src/luthien_proxy/`): Unified FastAPI + LiteLLM integration
  - Anthropic Messages API compatibility
  - Event-driven policy system with streaming support
  - OpenTelemetry instrumentation for observability

- **Orchestration** (`src/luthien_proxy/orchestration/`): Request processing coordination
  - `PolicyOrchestrator` coordinates the streaming pipeline
  - Real-time event publishing for UI updates
  - Trace context propagation

- **Policy System** (`src/luthien_proxy/policies/`): Event-driven policy framework
  - `SimplePolicy` - Base class for simple request/response policies
  - Examples: NoOpPolicy, AllCapsPolicy, DebugLoggingPolicy, ToolCallJudgePolicy

- **Policy Core** (`src/luthien_proxy/policy_core/`): Policy protocol and contexts
  - Policy protocol definitions
  - Request/response contexts for policy processing
  - Chunk builders for streaming

- **Streaming** (`src/luthien_proxy/streaming/`): Streaming support
  - Policy executor for stream processing
  - Client formatters for Anthropic format

- **UI** (`src/luthien_proxy/ui/`): Real-time monitoring and debugging
  - `/history` - Conversation history view
  - `/conversation/live/{id}` - Live conversation streaming view
  - Debug endpoints for inspection

**Documentation**:

- **Architecture overview**: [ARCHITECTURE.md](ARCHITECTURE.md) - How the codebase is structured, how requests flow, where to find things
- **Start here**: [Development docs index](dev/README.md) - Guide to all documentation
- Request processing architecture: [dev/REQUEST_PROCESSING_ARCHITECTURE.md](dev/REQUEST_PROCESSING_ARCHITECTURE.md) - How requests flow through the system
- Live policy updates: [dev/LIVE_POLICY_DEMO.md](dev/LIVE_POLICY_DEMO.md) - Switching policies without restart in Claude Code
- Observability: [dev/observability.md](dev/observability.md) - Tracing and monitoring
- Viewing traces: [dev/VIEWING_TRACES_GUIDE.md](dev/VIEWING_TRACES_GUIDE.md) - Using Tempo
- Context files: [dev/context/](dev/context/) - Architectural patterns, decisions, and gotchas

## Endpoints

### Gateway (<http://localhost:8000>)

**API Endpoints:**

- `POST /v1/messages` - Anthropic Messages API (streaming and non-streaming)
- `GET /health` - Health check

**UI Endpoints:**

- `GET /history` - Conversation history view (HTML)
- `GET /conversation/live/{id}` - Live conversation view with SSE streaming
- `GET /config` - Config dashboard (all settings with provenance)
- `GET /debug` - Debug information viewer

**Authentication:**

All API requests require the `Authorization: Bearer <PROXY_API_KEY>` header.

### Admin API

Admin endpoints manage policies and configuration at runtime. All admin requests require the `Authorization: Bearer <ADMIN_API_KEY>` header.

**Config dashboard API:**

```bash
# View all config with provenance (CLI/env/DB/default)
curl http://localhost:8000/api/admin/config \
  -H "Authorization: Bearer admin-dev-key"

# Set a DB-settable config value
curl -X PUT http://localhost:8000/api/admin/config/dogfood_mode \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{"value": true}'

# Reset a DB override (fall back to env or default)
curl -X DELETE http://localhost:8000/api/admin/config/dogfood_mode \
  -H "Authorization: Bearer admin-dev-key"
```

**Policy management:**

```bash
curl http://localhost:8000/api/admin/policy/current \
  -H "Authorization: Bearer admin-dev-key"

curl -X POST http://localhost:8000/api/admin/policy/set \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{
    "policy_class_ref": "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
    "config": {
      "model": "claude-haiku-4-5",
      "probability_threshold": 0.99,
      "temperature": 0.0,
      "max_tokens": 256
    }
  }'

curl http://localhost:8000/api/admin/policy/list \
  -H "Authorization: Bearer admin-dev-key"
```

## Configuration

All config is defined in `src/luthien_proxy/config_fields.py` — single source of truth for every gateway setting.

**Resolution priority:** CLI args > environment variables / `.env` file > database (`gateway_config` table) > defaults.

**Adding a new config value:**
1. Add a `ConfigFieldMeta` to `CONFIG_FIELDS` in `config_fields.py`
2. Run `scripts/dev_checks.sh` (or manually: `uv run python scripts/generate_settings.py` and `uv run python scripts/generate_env_example.py > .env.example`)

Both `settings.py` and `.env.example` are auto-generated — don't edit them by hand.

**CLI overrides:** All settings accept CLI flags (auto-generated from field names):
```bash
python -m luthien_proxy.main --gateway-port 9000 --log-level debug --dogfood-mode true
```

**DB-settable fields** (marked `db_settable=True`) can be changed at runtime via the admin API or the `/config` dashboard. Changes persist across restarts.

**Config dashboard:** Visit `/config` in the admin UI to see all active config with color-coded provenance badges (which source each value came from) and inline editing.

## Policy System

The gateway uses an event-driven policy architecture with streaming support.

### Key Components

- `src/luthien_proxy/policy_core/base_policy.py` - Abstract policy interface
- `src/luthien_proxy/policies/simple_policy.py` - Base class for custom policies
- `src/luthien_proxy/orchestration/policy_orchestrator.py` - Policy orchestration
- `src/luthien_proxy/gateway_routes.py` - API endpoint handlers with policy integration
- `config/policy_config.yaml` - Policy configuration

### Creating Custom Policies

Subclass `SimplePolicy` for basic request/response transformations. See `src/luthien_proxy/policies/` for examples.

## Troubleshooting

### Tests failing

```bash
# Check service health
curl http://localhost:8000/health

# View logs (local mode)
luthien logs

# View logs (Docker Compose mode)
docker compose logs gateway | tail -50
```

## Observability (Optional)

The gateway supports **OpenTelemetry** for distributed tracing and log correlation.

By default, the gateway runs **without** the observability stack. To enable it:

```bash
# Start observability stack (Tempo for distributed tracing)
./scripts/observability.sh up -d

# The gateway will automatically detect and use the observability stack
# Tempo HTTP API available at http://localhost:3200
```

The observability stack is completely optional and does not affect core functionality.

### Features

- **Distributed tracing** with OpenTelemetry and Tempo
- **Structured logging** with trace context (trace_id, span_id)
- **Real-time conversation view** at `/conversation/live/{id}`

### Observability Configuration

OpenTelemetry is configured via the standard config system (see Configuration section above). Key env vars:

```bash
OTEL_ENABLED=true                           # Enable tracing
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
SERVICE_NAME=luthien-proxy
```

### Documentation

- **Usage guide:** [dev/observability.md](dev/observability.md)
- **Conventions:** [dev/context/otel-conventions.md](dev/context/otel-conventions.md)

### Services

When observability is enabled:

- **Tempo** at http://localhost:3200 (trace storage and query via HTTP API)

## Releasing

Releases are automated via GitHub Actions. The workflow:

1. **During development**: Add entries under `## Unreleased` in `CHANGELOG.md` as features land. A CI check posts a one-time reminder on PRs that don't update the changelog (skip with the `skip-changelog` or `chore` label).
2. **To release**: Rename `## Unreleased` to `## vX.Y.Z` in `CHANGELOG.md`, commit, then push a tag:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
3. **Automated**: The release workflow builds the package with `uv build` and creates a GitHub Release with the changelog notes and dist artifacts.
