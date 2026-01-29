# Luthien Control

**Enforce rules on AI coding agents.** Luthien is a proxy that sits between your AI assistant (Claude Code, Codex, etc.) and the LLM backend, letting you intercept, inspect, and modify every request and response.

## What Can You Do With This?

**Write custom policies in Python** that run on every LLM interaction:

```python
from luthien_proxy.policies.simple_judge_policy import SimpleJudgePolicy

class MyPolicy(SimpleJudgePolicy):
    """Block dangerous commands before they execute."""

    RULES = [
        "Never allow 'rm -rf' commands",
        "Block requests to delete production data",
        "Require approval for any AWS credential access"
    ]
    # That's it! The LLM judge evaluates every request/response against your rules.
```

**Real-world use cases:**
- Block dangerous shell commands before execution
- Require human approval for sensitive operations
- Log every tool call for compliance/audit
- Replace AI-isms in responses (em-dashes → hyphens)
- Route requests to different models based on task type
- Enforce coding standards automatically

**Built-in features:**
- Real-time activity monitor — watch requests flow through
- Policy hot-reload — switch policies without restart
- Streaming support — works with Claude Code's streaming responses
- OpenAI & Anthropic compatible — drop-in proxy for both APIs

---

## Quick Start

**Point your AI assistant at the proxy with 2 environment variables:**

```bash
export ANTHROPIC_BASE_URL=http://localhost:8741/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
```

That's it. Your existing Claude Code (or any Anthropic-compatible client) now routes through Luthien.

### Start the Proxy

```bash
git clone https://github.com/LuthienResearch/luthien-proxy
cd luthien-proxy
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY (your real Anthropic key)
# Optional: change GATEWAY_PORT if 8741 conflicts with something

docker compose up -d
```

**What this starts (all in Docker):**
| Service | Port | Description |
|---------|------|-------------|
| Gateway | 8741 | The proxy — point your client here |
| PostgreSQL | 5432 | Stores conversation events |
| Redis | 6379 | Powers real-time activity streaming |
| Local LLM | 11434 | Ollama for local judge policies |

*Port conflicts? Change `GATEWAY_PORT` in `.env`*

### Verify It Works

```bash
curl http://localhost:8741/health
```

Then launch Claude Code with the env vars above and make a request. You should see it in the activity monitor:

```
http://localhost:8741/activity/monitor
```

---

## Create Your Own Policy

This is the power feature. Policies are Python classes that hook into the request/response lifecycle:

```python
# src/luthien_proxy/policies/my_custom_policy.py

from luthien_proxy.policies.simple_policy import SimplePolicy

class DeSloppify(SimplePolicy):
    """Remove AI-isms from responses."""

    async def on_response_complete(self, context):
        # Replace em-dashes with regular dashes
        content = context.response_content
        content = content.replace("—", "-")
        content = content.replace("–", "-")
        return context.with_modified_content(content)
```

For LLM-based rule enforcement, use `SimpleJudgePolicy`:

```python
from luthien_proxy.policies.simple_judge_policy import SimpleJudgePolicy

class SafetyPolicy(SimpleJudgePolicy):
    """Use an LLM judge to evaluate requests against rules."""

    RULES = [
        "Never execute commands that delete files recursively",
        "Block any request to access environment variables containing 'SECRET' or 'KEY'",
        "Require explicit confirmation for git push --force"
    ]
```

Restart the gateway (`docker compose restart gateway`) and your policy appears in the Policy Config UI.

**Policy lifecycle hooks:**
- `on_request` — Before sending to LLM
- `on_chunk` — Each streaming chunk (for real-time modifications)
- `on_block_complete` — After a complete message/tool_use block
- `on_response_complete` — After full response received

See `src/luthien_proxy/policies/` for more examples.

---

## What You Get

- **Gateway** (OpenAI/Anthropic-compatible) at <http://localhost:8741>
- **PostgreSQL** and **Redis** fully configured
- **Local LLM** (Ollama) at <http://localhost:11434>
- **Real-time monitoring** at <http://localhost:8741/activity/monitor>
- **Policy management UI** at <http://localhost:8741/policy-config>

The gateway provides:

- OpenAI Chat Completions API (`/v1/chat/completions`)
- Anthropic Messages API (`/v1/messages`)
- Integrated policy enforcement via control plane
- Support for streaming and non-streaming requests
- Hot-reload policy switching (no restart needed)

## Prerequisites

- Docker
- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Development

```bash
# After code changes, restart the gateway
docker compose restart gateway

# Run unit tests
uv run pytest tests/unit_tests

# Run integration tests
uv run pytest tests/integration_tests

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

## Observability (Optional)

The gateway supports **OpenTelemetry** for distributed tracing and log correlation.

By default, the gateway runs **without** the observability stack. To enable it:

```bash
# Start observability stack (Tempo, Loki, Promtail, Grafana)
./scripts/observability.sh up -d

# The gateway will automatically detect and use the observability stack

# Access Grafana at http://localhost:3000
# Username: admin, Password: admin
```

The observability stack is completely optional and does not affect core functionality.

### Features

- **Distributed tracing** with OpenTelemetry → Grafana Tempo
- **Structured logging** with trace context (trace_id, span_id)
- **Log-trace correlation** in Grafana
- **Real-time activity feed** at `/activity/monitor`
- **Pre-built dashboard** for traces and logs

### Configuration

OpenTelemetry is enabled by default. To configure the endpoint in `.env`:

```bash
# OpenTelemetry endpoint (enabled by default)
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317

# Optional: customize service metadata
SERVICE_NAME=luthien-proxy
SERVICE_VERSION=2.0.0
ENVIRONMENT=development

# To disable tracing, set:
# OTEL_ENABLED=false
```

### Documentation

- **Usage guide:** [dev/observability.md](dev/observability.md)
- **Conventions:** [dev/context/otel-conventions.md](dev/context/otel-conventions.md)
- **Dashboard:** Import `observability/grafana-dashboards/luthien-traces.json` in Grafana

### Services

When observability is enabled:

- **Grafana** at http://localhost:3000 (dashboards and visualization)
- **Tempo** at http://localhost:3200 (trace storage and query)
- **Loki** at http://localhost:3100 (log aggregation)

## Configuration

Copy `.env.example` to `.env` and configure your environment:

### Required Configuration

```bash
# Upstream LLM Provider API Keys
OPENAI_API_KEY=your_openai_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Gateway Authentication
PROXY_API_KEY=sk-luthien-dev-key     # API key for clients to access the proxy
ADMIN_API_KEY=admin-dev-key          # API key for admin/policy management UI
```

### Core Infrastructure

```bash
# Database
DATABASE_URL=postgresql://luthien:password@db:5432/luthien_control

# Redis (for real-time activity streaming)
REDIS_URL=redis://redis:6379

# Gateway
GATEWAY_HOST=localhost
GATEWAY_PORT=8741
```

### Policy Configuration

```bash
# Policy loading strategy
# Options: "db", "file", "db-fallback-file" (recommended), "file-fallback-db"
POLICY_SOURCE=db-fallback-file

# Path to YAML policy file (when POLICY_SOURCE includes "file")
POLICY_CONFIG=/app/config/policy_config.yaml
```

### Observability (Optional)

```bash
# OpenTelemetry tracing
OTEL_ENABLED=true                                    # Toggle tracing
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317       # OTLP endpoint

# Service metadata for distributed tracing
SERVICE_NAME=luthien-proxy
SERVICE_VERSION=2.0.0
ENVIRONMENT=development

# Grafana for viewing traces
GRAFANA_URL=http://localhost:3000
```

### LLM Judge Policies (Optional)

```bash
# Configuration for judge-based policies (ToolCallJudgePolicy, SimpleJudgePolicy)
LLM_JUDGE_MODEL=openai/gpt-4                         # Model for judge
LLM_JUDGE_API_BASE=http://localhost:11434/v1         # API base URL
LLM_JUDGE_API_KEY=your_judge_api_key                 # API key for judge
```

See `.env.example` for all available options and defaults.

### Policy File Format

The gateway loads policies from `POLICY_CONFIG` (defaults to `config/policy_config.yaml`).

Example policy configuration:

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_v3:ToolCallJudgeV3Policy"
  config:
    model: "ollama/gemma2:2b"
    api_base: "http://local-llm:11434"
    api_key: "ollama"
    probability_threshold: 0.6
    temperature: 0.0
    max_tokens: 256
```

Available policies in `src/luthien_proxy/policies/`:

- `noop_policy.py` - Pass-through (no filtering)
- `all_caps_policy.py` - Simple transformation example
- `debug_logging_policy.py` - Logs requests/responses for debugging
- `tool_call_judge_policy.py` - AI-based tool call safety evaluation
- `simple_policy.py` - Base class for custom policies
- `simple_judge_policy.py` - Base class for LLM-based rule enforcement

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
  - OpenAI Chat Completions API compatibility
  - Anthropic Messages API compatibility
  - Event-driven policy system with streaming support
  - OpenTelemetry instrumentation for observability

- **Orchestration** (`src/luthien_proxy/orchestration/`): Request processing coordination
  - `PolicyOrchestrator` coordinates the streaming pipeline
  - Real-time event publishing for UI updates
  - Trace context propagation

- **Policy System** (`src/luthien_proxy/policies/`): Event-driven policy framework
  - `SimplePolicy` - Base class for simple request/response policies
  - `SimpleJudgePolicy` - Base class for LLM-based rule enforcement
  - Examples: NoOpPolicy, AllCapsPolicy, DebugLoggingPolicy, ToolCallJudgePolicy

- **Policy Core** (`src/luthien_proxy/policy_core/`): Policy protocol and contexts
  - Policy protocol definitions
  - Request/response contexts for policy processing
  - Chunk builders for streaming

- **Streaming** (`src/luthien_proxy/streaming/`): Streaming support
  - Policy executor for stream processing
  - Client formatters for OpenAI/Anthropic formats

- **UI** (`src/luthien_proxy/ui/`): Real-time monitoring and debugging
  - `/activity/monitor` - Live activity feed
  - `/activity/live` - WebSocket activity stream
  - Debug endpoints for inspection

**Documentation**:

- **Start here**: [Development docs index](dev/README.md) - Guide to all documentation
- Request processing architecture: [dev/REQUEST_PROCESSING_ARCHITECTURE.md](dev/REQUEST_PROCESSING_ARCHITECTURE.md) - How requests flow through the system
- Live policy updates: [dev/LIVE_POLICY_DEMO.md](dev/LIVE_POLICY_DEMO.md) - Switching policies without restart in Claude Code
- Observability: [dev/observability.md](dev/observability.md) - Tracing and monitoring
- Viewing traces: [dev/VIEWING_TRACES_GUIDE.md](dev/VIEWING_TRACES_GUIDE.md) - Using Grafana/Tempo
- Context files: [dev/context/](dev/context/) - Architectural patterns, decisions, and gotchas

## Endpoints

### Gateway (<http://localhost:8741>)

**API Endpoints:**

- `POST /v1/chat/completions` — OpenAI Chat Completions API (streaming and non-streaming)
- `POST /v1/messages` — Anthropic Messages API (streaming and non-streaming)
- `GET /health` — Health check

**UI Endpoints:**

- `GET /activity/monitor` — Real-time activity monitor (HTML)
- `GET /activity/live` — WebSocket activity stream (JSON)
- `GET /debug` — Debug information viewer

**Authentication:**

All API requests require the `Authorization: Bearer <PROXY_API_KEY>` header.

### Admin API

Admin endpoints manage policies at runtime without requiring a restart. All admin requests require the `Authorization: Bearer <ADMIN_API_KEY>` header.

**Get current policy:**

```bash
curl http://localhost:8741/admin/policy/current \
  -H "Authorization: Bearer admin-dev-key"
```

**Create a named policy instance:**

```bash
curl -X POST http://localhost:8741/admin/policy/create \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{
    "name": "my-policy",
    "policy_class_ref": "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
    "config": {
      "model": "openai/gpt-4o-mini",
      "probability_threshold": 0.99,
      "temperature": 0.0,
      "max_tokens": 256
    }
  }'
```

**Activate a policy:**

```bash
curl -X POST http://localhost:8741/admin/policy/activate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{"name": "my-policy"}'
```

**List available policy classes:**

```bash
curl http://localhost:8741/admin/policy/list \
  -H "Authorization: Bearer admin-dev-key"
```

**List saved policy instances:**

```bash
curl http://localhost:8741/admin/policy/instances \
  -H "Authorization: Bearer admin-dev-key"
```

## Policy System

The gateway uses an event-driven policy architecture with streaming support.

### Key Components

- `src/luthien_proxy/policies/base_policy.py` - Abstract policy interface
- `src/luthien_proxy/policies/simple_policy.py` - Base class for custom policies
- `src/luthien_proxy/policies/simple_judge_policy.py` - Base class for LLM-based rule enforcement
- `src/luthien_proxy/orchestration/policy_orchestrator.py` - Policy orchestration
- `src/luthien_proxy/gateway_routes.py` - API endpoint handlers with policy integration
- `config/policy_config.yaml` - Policy configuration

### Creating Custom Policies

Subclass `SimplePolicy` for basic request/response transformations, or `SimpleJudgePolicy` for LLM-based rule enforcement. See `src/luthien_proxy/policies/` for examples.

### Testing

```bash
# Start the gateway
./scripts/quick_start.sh

# Run automated tests
./scripts/test_gateway.sh

# View logs
docker compose logs -f gateway
```

## Troubleshooting

### Gateway not starting

```bash
# Check service status
docker compose ps

# View gateway logs
docker compose logs gateway

# Restart gateway
docker compose restart gateway

# Full restart
docker compose down && ./scripts/quick_start.sh
```

### API requests failing

1. **Check API key**: Ensure `Authorization: Bearer <PROXY_API_KEY>` header is set
2. **Check upstream credentials**: Verify `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` in `.env`
3. **Check logs**: `docker compose logs -f gateway`

### Tests failing

```bash
# Ensure services are running
docker compose ps

# Check service health
curl http://localhost:8741/health

# View detailed logs
docker compose logs gateway | tail -50
```

### Database connection issues

```bash
# Check database is running
docker compose ps db

# Restart database
docker compose restart db

# Re-run migrations
docker compose run --rm db-migrations
```

## License

Apache License 2.0
