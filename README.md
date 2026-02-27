# Luthien
### Let AI code. Stay in control.

[What is it?](#what-is-it) | [What does it look like?](#what-does-it-look-like) | [How does it work?](#how-does-it-work) | [What can it do?](#what-can-it-do) | [Quick start](#quick-start)

---

## What is it?

Luthien is a proxy that sits between Claude Code (client) and the Claude API backend. It logs every request and response and enables you to set arbitrary rules/policies that can block dangerous operations, confirm the output is what you asked for, adheres to your CLAUDE.md, and doesn't contain suspicious stuff — all without changing your dev setup.

---

## What does it look like?

<table>
<tr>
<th width="50%">Before</th>
<th width="50%">After</th>
</tr>
<tr>
<td>

<img src="assets/readme/terminal-without-luthien.svg?v=17" alt="Before: Claude Code runs pip install despite CLAUDE.md rules" width="100%">

</td>
<td>

<img src="assets/readme/terminal-with-luthien.svg?v=17" alt="After: pip install is blocked by Luthien, Claude retries with uv add" width="100%">

</td>
</tr>
</table>

> ⚠️ Luthien is in active development. [Star this repo](https://github.com/LuthienResearch/luthien-proxy) to follow updates, or [Watch > Releases](https://github.com/LuthienResearch/luthien-proxy/subscription) to get notified on new versions.
>
> Found a bug or have a question? [Open an issue](https://github.com/LuthienResearch/luthien-proxy/issues).

---

## How does it work?

<img src="assets/readme/how-it-works.svg" alt="How Luthien works: sits between Claude Code and the API, logs everything, enforces your rules" width="100%">

Luthien runs on your machine or your cloud account.

---

## What can it do?

- **Block dangerous operations:** `rm -rf`, `git push --force`, dropping database tables
- **Enforce package standards:** block `pip install`, suggest `uv add` instead
- **Clean up AI writing tics:** remove em dashes, curly quotes, over-bulleting
- **Enforce scope boundaries:** only allow changes to files mentioned in the request
- **Log everything:** get a URL to a live-updating log of your full agent conversation

<details>
<summary><b>Example: PipBlockPolicy (click to expand)</b></summary>

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    probability_threshold: 0.95
    judge_instructions: |
      Block any `pip install`, `pip3 install`, or `python -m pip install` tool call.
      Suggest using `uv add` instead.
```

</details>

Every policy action is logged. Measure what got blocked, track false positives, monitor latency overhead.

---

## Quick Start

**Prerequisites:**
[Docker](https://www.docker.com/products/docker-desktop/) must be running (or `brew install --cask docker`).
Install [uv](https://docs.astral.sh/uv/) if you haven't: `curl -LsSf https://astral.sh/uv/install.sh | sh`

### 1. Clone the repo

```bash
git clone https://github.com/LuthienResearch/luthien-proxy && cd luthien-proxy
```

### 2. Add your Anthropic API key to `.env`

```bash
cp .env.example .env && echo "ANTHROPIC_API_KEY=your-key-here" >> .env
```

Replace `your-key-here` with your key from [console.anthropic.com](https://console.anthropic.com/settings/keys).

### 3. Run quick start script

```bash
./scripts/quick_start.sh
```

### 4. Launch Claude Code through the proxy

```bash
./scripts/launch_claude_code.sh
```

🚀🎉 All requests and responses are now logged through the proxy.


### 5. See conversation history and raw logs

See your conversation history: <http://localhost:8000/history>

Or see full JSONLs in the activity monitor: <http://localhost:8000/activity/monitor>

### 6. Set up a DeSlop policy (string replacement)

| Find | Replace with |
|------|-------------|
| `—` (em dash) | `-` (hyphen) |

Runs on every LLM response. Switch policies at [localhost:8000/policy-config](http://localhost:8000/policy-config).

<details>
<summary><b>See the code (click to expand)</b></summary>

```python
from luthien_proxy.policies.simple_policy import SimplePolicy

class DeSlop(SimplePolicy):
    async def simple_on_response_content(self, content, ctx):  # runs on every LLM response before it reaches Claude Code
        return content.replace("\u2014", "-").replace("\u2013", "-")  # em dash → hyphen, en dash → hyphen
```

</details>

### 7. Set up an LLM-as-judge policy

Luthien can call an LLM (like Haiku) to evaluate your rules on every request and response.

<details>
<summary><b>Did it do what I asked?</b></summary>

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    probability_threshold: 0.8
    judge_instructions: |
      Check if the agent completed what the user asked for.
      Flag if it claims "Done" but leaves TODOs, stubs, or placeholders.
```

</details>

<details>
<summary><b>Did it follow CLAUDE.md?</b></summary>

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    probability_threshold: 0.85
    judge_instructions: |
      Enforce explicit rules from CLAUDE.md for this repo.
      Flag any command or response that violates those rules.
```

</details>

<details>
<summary><b>Did it do something suspicious?</b></summary>

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    probability_threshold: 0.7
    judge_instructions: |
      Block recursive deletes on project directories.
      Block force-pushing to protected branches.
      Flag suspicious command patterns or hidden side effects.
```

</details>

---

## What You Get

- **Gateway** (OpenAI/Anthropic-compatible) at <http://localhost:8000>
- **PostgreSQL** and **Redis** fully configured
- **Real-time monitoring** at <http://localhost:8000/activity/monitor>
- **Policy management UI** at <http://localhost:8000/policy-config>

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
# Start observability stack (Tempo for distributed tracing)
./scripts/observability.sh up -d

# The gateway will automatically detect and use the observability stack
# Tempo HTTP API available at http://localhost:3200
```

The observability stack is completely optional and does not affect core functionality.

### Features

- **Distributed tracing** with OpenTelemetry and Tempo
- **Structured logging** with trace context (trace_id, span_id)
- **Real-time activity feed** at `/activity/monitor`

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

### Services

When observability is enabled:

- **Tempo** at http://localhost:3200 (trace storage and query via HTTP API)

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
GATEWAY_PORT=8000
```

### Policy Configuration

```bash
# Policy loading strategy
# Options: "db", "file", "db-fallback-file" (recommended), "file-fallback-db"
POLICY_SOURCE=db-fallback-file

# Path to YAML policy file (when POLICY_SOURCE includes "file")
POLICY_CONFIG=/app/config/policy_config.yaml
```

### LLM Judge Policies (Optional)

```bash
# Configuration for judge-based policies (ToolCallJudgePolicy)
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
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    model: "openai/gpt-4o-mini"
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
- Viewing traces: [dev/VIEWING_TRACES_GUIDE.md](dev/VIEWING_TRACES_GUIDE.md) - Using Tempo
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

- `src/luthien_proxy/policies/base_policy.py` - Abstract policy interface
- `src/luthien_proxy/policies/simple_policy.py` - Base class for custom policies
- `src/luthien_proxy/orchestration/policy_orchestrator.py` - Policy orchestration
- `src/luthien_proxy/gateway_routes.py` - API endpoint handlers with policy integration
- `config/policy_config.yaml` - Policy configuration

### Creating Custom Policies

Subclass `SimplePolicy` for basic request/response transformations. See `src/luthien_proxy/policies/` for examples.

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
