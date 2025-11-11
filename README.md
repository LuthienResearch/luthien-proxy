# Luthien Control

Redwood-style AI Control as an LLM proxy for production agentic deployments.

## Quick Start

```bash
# 1. Start everything
./scripts/quick_start.sh

# 2. Test it works
./scripts/test_v2_gateway.sh
```

You now have:

- **V2 Gateway** (OpenAI-compatible) at <http://localhost:8000>
- **PostgreSQL** and **Redis** fully configured
- **Local LLM** (Ollama) at <http://localhost:11434>

The V2 gateway provides:

- OpenAI Chat Completions API (`/v1/chat/completions`)
- Anthropic Messages API (`/v1/messages`)
- Integrated policy enforcement via control plane
- Support for streaming and non-streaming requests

## Prerequisites

- Docker
- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Development

```bash
# After code changes, restart the V2 gateway
docker compose restart v2-gateway

# Run unit tests
uv run pytest tests/unit_tests

# Run integration tests
uv run pytest tests/integration_tests

# Run e2e tests (slow, use sparingly)
uv run pytest -m e2e

# Test the V2 gateway
./scripts/test_v2_gateway.sh

# Format and lint
./scripts/format_all.sh

# Full dev checks (format + lint + tests + type check)
./scripts/dev_checks.sh

# Type check only
uv run pyright
```

## Observability (Optional)

The V2 gateway supports **OpenTelemetry** for distributed tracing and log correlation.

By default, the V2 gateway runs **without** the observability stack. To enable it:

```bash
# Start observability stack (Tempo, Loki, Promtail, Grafana)
./scripts/observability.sh up -d

# The V2 gateway will automatically detect and use the observability stack

# Access Grafana
open http://localhost:3000
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

Enable OpenTelemetry in `.env`:

```bash
OTEL_ENABLED=true
OTEL_ENDPOINT=http://tempo:4317
SERVICE_NAME=luthien-proxy-v2
SERVICE_VERSION=2.0.0
ENVIRONMENT=development
```

### Documentation

- **Usage guide:** [dev/observability-v2.md](dev/observability-v2.md)
- **Conventions:** [dev/context/otel-conventions.md](dev/context/otel-conventions.md)
- **Dashboard:** Import `observability/grafana-dashboards/luthien-traces.json` in Grafana

### Services

When observability is enabled:

- **Grafana** at http://localhost:3000 (dashboards and visualization)
- **Tempo** at http://localhost:3200 (trace storage and query)
- **Loki** at http://localhost:3100 (log aggregation)

## Configuration

Copy `.env.example` to `.env` and add your API keys:

```bash
# Required API keys for upstream providers
OPENAI_API_KEY=your_openai_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Gateway configuration
PROXY_API_KEY=sk-luthien-dev-key  # API key for accessing the gateway
GATEWAY_PORT=8000               # Gateway port
POLICY_CONFIG=/app/config/policy_config.yaml  # Policy configuration
```

### Policy Configuration

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

Available policies in `src/luthien_proxy/v2/policies/`:

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

The V2 architecture integrates everything into a single FastAPI application:

- **V2 Gateway** (`src/luthien_proxy/v2/`): Unified FastAPI + LiteLLM integration
  - OpenAI Chat Completions API compatibility
  - Anthropic Messages API compatibility
  - Integrated control plane for policy enforcement
  - Event-driven policy system with streaming support
  - OpenTelemetry instrumentation for observability

- **Control Plane** (`src/luthien_proxy/v2/control/`): Synchronous policy orchestration
  - Processes requests through configured policies
  - Real-time event publishing for UI updates
  - Trace context propagation

- **Policy System** (`src/luthien_proxy/v2/policies/`): Event-driven policy framework
  - Stream-aware policy interface
  - Buffering and transformation capabilities
  - Examples: NoOp, ToolCallJudge, UppercaseNthWord

- **UI** (`src/luthien_proxy/v2/ui/`): Real-time monitoring and debugging
  - `/activity/monitor` - Live activity feed
  - `/activity/live` - WebSocket activity stream
  - Debug endpoints for inspection

**Documentation**:

- **Start here**: [Development docs index](dev/README.md) - Guide to all documentation
- Request processing architecture: [dev/REQUEST_PROCESSING_ARCHITECTURE.md](dev/REQUEST_PROCESSING_ARCHITECTURE.md) - How requests flow through the system
- Observability: [dev/observability-v2.md](dev/observability-v2.md) - Tracing and monitoring
- Viewing traces: [dev/VIEWING_TRACES_GUIDE.md](dev/VIEWING_TRACES_GUIDE.md) - Using Grafana/Tempo
- Context files: [dev/context/](dev/context/) - Architectural patterns, decisions, and gotchas

## Endpoints

### V2 Gateway (<http://localhost:8000>)

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

## V2 Policy System

The V2 gateway uses an event-driven policy architecture with streaming support.

### Key Components

- `src/luthien_proxy/v2/policies/base.py` - Abstract policy interface
- `src/luthien_proxy/v2/control/synchronous_control_plane.py` - Policy orchestration
- `src/luthien_proxy/v2/gateway_routes.py` - API endpoint handlers with policy integration
- `config/policy_config.yaml` - Policy configuration

### Creating Custom Policies

Policies implement the `LuthienPolicy` interface or use the event-driven DSL:

```python
from luthien_proxy.streaming import EventDrivenPolicy, StreamingContext

class MyPolicy(EventDrivenPolicy):
    async def on_content_chunk(self, content: str, raw_chunk, state, context: StreamingContext):
        # Process content chunks
        await context.send(raw_chunk)  # Forward or transform
```

See `src/luthien_proxy/v2/policies/` for examples:

- `noop.py` - Simple pass-through
- `uppercase_nth_word.py` - Content transformation
- `tool_call_judge_v3.py` - AI-based safety evaluation

### Testing

```bash
# Start the gateway
./scripts/quick_start.sh

# Run automated tests
./scripts/test_v2_gateway.sh

# View logs
docker compose logs -f v2-gateway
```

## Troubleshooting

### Gateway not starting

```bash
# Check service status
docker compose ps

# View gateway logs
docker compose logs v2-gateway

# Restart gateway
docker compose restart v2-gateway

# Full restart
docker compose down && ./scripts/quick_start.sh
```

### API requests failing

1. **Check API key**: Ensure `Authorization: Bearer <PROXY_API_KEY>` header is set
2. **Check upstream credentials**: Verify `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` in `.env`
3. **Check logs**: `docker compose logs -f v2-gateway`

### Tests failing

```bash
# Ensure services are running
docker compose ps

# Check service health
curl http://localhost:8000/health

# View detailed logs
docker compose logs v2-gateway | tail -50
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
