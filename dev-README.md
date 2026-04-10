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

The gateway is a single FastAPI application:

- **Gateway** (`src/luthien_proxy/`): Anthropic Messages API proxy
  - Anthropic `/v1/messages` endpoint (streaming and non-streaming)
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
- `GET /debug` - Debug information viewer

**Authentication (two layers):**

- **Upstream (Anthropic)**: By default, the gateway passes through client credentials (OAuth tokens or API keys) to Anthropic. Optionally set `ANTHROPIC_API_KEY` in `.env` to use a single API key for all requests.
- **Gateway**: Optionally set `PROXY_API_KEY` in `.env` to require clients to authenticate with the gateway itself.

### Admin API

Admin endpoints manage policies at runtime without requiring a restart. All admin requests require the `Authorization: Bearer <ADMIN_API_KEY>` header.

**Get current policy:**

```bash
curl http://localhost:8000/api/admin/policy/current \
  -H "Authorization: Bearer admin-dev-key"
```

**Set the active policy:**

```bash
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
```

**List available policy classes:**

```bash
curl http://localhost:8000/api/admin/policy/list \
  -H "Authorization: Bearer admin-dev-key"
```

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

### Configuration

OpenTelemetry is enabled by default. To configure the endpoint in `.env`:

```bash
# OpenTelemetry endpoint (enabled by default)
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317

# Optional: customize service metadata
SERVICE_NAME=luthien-proxy
ENVIRONMENT=development

# To disable tracing, set:
# OTEL_ENABLED=false
```

### Documentation

- **Usage guide:** [dev/observability.md](dev/observability.md)
- **Conventions:** [dev/context/otel-conventions.md](dev/context/otel-conventions.md)

### Services

When observability is enabled:

- **Tempo** at http://localhost:3200 (trace storage and query via HTTP API)

## Releasing

Patch releases happen automatically when PRs merge to main — no manual steps required.

### Every PR: add a changelog fragment

Create `changelog.d/<branch-name>.md`:

```markdown
---
category: Features|Fixes|Refactors|Chores & Docs
pr: 123
---

**Short title**: Description of the change
```

CI reminds you if you forget. See `changelog.d/README.md` for details.

### Bumping minor or major version

The auto-tag workflow always increments the patch of the latest `v*` tag. To bump minor or major, create the tag yourself — the next merge increments from there:

```bash
git checkout main && git pull
git tag v<MAJOR>.<MINOR>.0
git push origin v<MAJOR>.<MINOR>.0
```

### What the automation does

On every merge to main, `auto-tag-proxy.yml`:
1. Skips if only CLI files changed
2. Runs unit tests
3. Compiles `changelog.d/` fragments into `CHANGELOG.md`
4. Cuts `## Unreleased` → `## X.Y.Z | date`, commits with `[skip auto-tag-proxy]`
5. Tags `vX.Y.Z`, pushes — triggers GitHub Release + Docker image publish to GHCR

Key files: `.github/workflows/auto-tag-proxy.yml`, `scripts/compile_changelog.py --cut-release`.

### CLI releases are separate

`luthien-cli` auto-tags on `cli-v*` and publishes to PyPI via `auto-tag-cli.yml` → `release-cli.yml`. Independent of proxy releases.
