# Luthien Control

Redwood-style AI Control as an LLM proxy for production agentic deployments.

## Quick Start

### 1. Install and Start

```bash
# Install uv (if needed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and start everything
git clone https://github.com/LuthienResearch/luthien-proxy
cd luthien-proxy

# Configure API keys
cp .env.example .env
# Edit .env and add your keys:
#   OPENAI_API_KEY=sk-proj-...
#   ANTHROPIC_API_KEY=sk-ant-...

# Start the stack
./scripts/quick_start.sh
```

### 2. Use Claude Code or Codex through the Proxy

Launch your AI assistant through the proxy using the built-in scripts:

**Claude Code:**

```bash
./scripts/launch_claude_code.sh
```

**Codex:**

```bash
./scripts/launch_codex.sh
```

These scripts automatically configure the proxy settings. All requests now flow through the policy enforcement layer!

### 3. Log In to Admin UI

When you first visit any admin page (Activity Monitor, Policy Config, or Debug views), you'll be redirected to:

```
http://localhost:8000/login
```

**Default credentials (development):**
- Admin API Key: `admin-dev-key`

After logging in, your session persists across pages. Click "Sign Out" on any admin page to log out.

⚠️ **For production deployments**: Change `ADMIN_API_KEY` in your `.env` file before exposing to a network.

### 4. Monitor Activity

Open the Activity Monitor in your browser to see requests in real-time:

```
http://localhost:8000/activity/monitor
```

Watch as requests flow through, see policy decisions, and inspect before/after diffs.

### 5. Select a Policy

Use the Policy Configuration UI to change policies without restart:

```
http://localhost:8000/policy-config
```

1. Browse available policies (NoOp, AllCaps, DebugLogging, etc.)
2. Click to select and activate
3. Test immediately - changes take effect instantly

### 6. Create Your Own Policy

Create a new policy by subclassing `SimpleJudgePolicy`:

```python
# src/luthien_proxy/policies/my_custom_policy.py

from luthien_proxy.policies.simple_judge_policy import SimpleJudgePolicy

class MyCustomPolicy(SimpleJudgePolicy):
    """Block dangerous commands before they execute."""

    RULES = [
        "Never allow 'rm -rf' commands",
        "Block requests to delete production data",
        "Prevent executing untrusted code"
    ]

    # That's it! SimpleJudgePolicy handles the LLM judge logic for you.
    # It evaluates both requests, responses, and tool calls against your rules.
```

Restart the gateway and your policy appears in the Policy Config UI automatically.

---

## What You Get

- **Gateway** (OpenAI/Anthropic-compatible) at <http://localhost:8000>
- **PostgreSQL** and **Redis** fully configured
- **Local LLM** (Ollama) at <http://localhost:11434>
- **Real-time monitoring** at <http://localhost:8000/activity/monitor>
- **Policy management UI** at <http://localhost:8000/policy-config>

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

Copy `.env.example` to `.env` and configure the following variables:

### API Keys

```bash
# Required: API keys for upstream LLM providers
OPENAI_API_KEY=your_openai_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Required: API key for clients to authenticate to the proxy
PROXY_API_KEY=sk-luthien-dev-key

# Required: API key for admin/policy management operations
ADMIN_API_KEY=admin-dev-key
```

### Gateway Configuration

```bash
GATEWAY_HOST=localhost
GATEWAY_PORT=8000
```

### Policy Configuration

```bash
# How to load and persist policies
# Options: "db", "file", "db-fallback-file", "file-fallback-db"
POLICY_SOURCE=db-fallback-file

# Path to YAML policy configuration file
POLICY_CONFIG=/app/config/policy_config.yaml
```

### Database Configuration

```bash
POSTGRES_USER=luthien
POSTGRES_PASSWORD=luthien_dev_password
POSTGRES_DB=luthien_control
POSTGRES_PORT=5432
DATABASE_URL=postgresql://luthien:luthien_dev_password@db:5432/luthien_control
```

### Redis Configuration

```bash
REDIS_URL=redis://redis:6379
REDIS_PORT=6379
```

### Local LLM Configuration

```bash
# Local LLM Gateway (OpenAI-compatible) for policy scoring
LOCAL_LLM_PORT=4010

# Ollama (local model host) port
OLLAMA_PORT=11434

# Test model used by scripts (set to a model available with your API keys)
TEST_MODEL=gpt-4o-mini
```

### Observability (Optional)

```bash
# OpenTelemetry endpoint (leave empty to disable tracing)
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317

# Grafana URL for viewing traces and logs
GRAFANA_URL=http://localhost:3000
```

### Policy YAML Files

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

- `noop.py` - Pass-through (no filtering)
- `event_based_noop.py` - Event-driven no-op (demonstrates DSL)
- `uppercase_nth_word.py` - Simple transformation example
- `tool_call_judge_v3.py` - AI-based tool call safety evaluation

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
  - Integrated control plane for policy enforcement
  - Event-driven policy system with streaming support
  - OpenTelemetry instrumentation for observability

- **Control Plane** (`src/luthien_proxy/control/`): Synchronous policy orchestration
  - Processes requests through configured policies
  - Real-time event publishing for UI updates
  - Trace context propagation

- **Policy System** (`src/luthien_proxy/policies/`): Event-driven policy framework
  - Stream-aware policy interface
  - Buffering and transformation capabilities
  - Examples: NoOp, ToolCallJudge, UppercaseNthWord

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

### Gateway (<http://localhost:8000>)

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
curl http://localhost:8000/admin/policy/current \
  -H "Authorization: Bearer admin-dev-key"
```

**Create a named policy instance:**

```bash
curl -X POST http://localhost:8000/admin/policy/create \
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
curl -X POST http://localhost:8000/admin/policy/activate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{"name": "my-policy"}'
```

**List available policy classes:**

```bash
curl http://localhost:8000/admin/policy/list \
  -H "Authorization: Bearer admin-dev-key"
```

**List saved policy instances:**

```bash
curl http://localhost:8000/admin/policy/instances \
  -H "Authorization: Bearer admin-dev-key"
```

## Policy System

The gateway uses an event-driven policy architecture with streaming support.

### Key Components

- `src/luthien_proxy/policies/base.py` - Abstract policy interface
- `src/luthien_proxy/control/synchronous_control_plane.py` - Policy orchestration
- `src/luthien_proxy/gateway_routes.py` - API endpoint handlers with policy integration
- `config/policy_config.yaml` - Policy configuration

### Creating Custom Policies

See the "Create Your Own Policy" section in [Quick Start](#5-create-your-own-policy) for a complete example of creating a custom policy with `SimpleJudgePolicy`.

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
curl http://localhost:8000/health

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
