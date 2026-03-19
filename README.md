# Luthien <!-- README v10.7 -->

### Claude Code builds. You stay in control.

[What does it look like?](#what-does-it-look-like) | [What can it do?](#what-can-it-do) | [How does it work?](#how-does-it-work) | [Quick start](#quick-start)

Open-source proxy that sits between Claude Code and the Anthropic API.
Logs every request. Enforces your rules.

---

## What does it look like?

Say your `CLAUDE.md` has this rule:

```
Python packages: use uv add, never pip install.
```

<table>
<tr>
<th width="50%">Without Luthien</th>
<th width="50%">With Luthien</th>
</tr>
<tr>
<td valign="top">

<img src="assets/readme/terminal-without-luthien.svg?v=19" alt="Without Luthien: Claude Code ignores your CLAUDE.md rules and you correct it manually" width="100%">

Claude ignores your CLAUDE.md rule and you correct it manually.

</td>
<td valign="top">

<img src="assets/readme/terminal-with-luthien.svg?v=19" alt="With Luthien: Luthien catches the violation and auto-corrects" width="100%">

Luthien catches the violation and auto-corrects. No human intervention needed.

</td>
</tr>
</table>

> :rotating_light: Luthien is in active development. [Star this repo](https://github.com/LuthienResearch/luthien-proxy) to follow updates, or [Watch > Releases](https://github.com/LuthienResearch/luthien-proxy/subscription) to get notified on new versions.
>
> Found a bug or have a question? [Open an issue](https://github.com/LuthienResearch/luthien-proxy/issues).

---

## What can it do?

### Enforce arbitrary rules/policies

- **Block dangerous operations** - `rm -rf`, `git push --force`, dropping database tables
- **Enforce package standards** - block `pip install`, suggest `uv add` instead
- **Clean up AI writing tics** - remove em dashes, curly quotes, over-bulleting
- **Enforce scope boundaries** - only allow changes to files mentioned in the request

**Example: ToolCallJudgePolicy** - an LLM judge that evaluates every tool call:

```yaml
# config/policy_config.yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    model: "openai/gpt-4o-mini"
    probability_threshold: 0.6  # block if judge confidence >= 60%
    judge_instructions: >
      Block any 'pip install' commands. Suggest 'uv add' instead.
      Block 'rm -rf' or any recursive delete on project directories.
      Block 'git push --force' to main or master.
```

### Log everything passing through the proxy

Every request and response between Claude Code and the Anthropic API is recorded automatically.

- **Live conversation view** - open [localhost:8000/history](http://localhost:8000/history) to see your full agent conversation in a readable format, updated in real time
- **Activity monitor** - open [localhost:8000/activity/monitor](http://localhost:8000/activity/monitor) to see raw JSON request/response pairs streaming through the proxy
- **Policy action log** - every policy decision (blocked, modified, or allowed) is recorded with the full context of what triggered it

This means you can answer questions like: what did Claude actually send to the API? Did the policy fire? What got blocked vs. allowed? Track false positives and monitor latency overhead - all from a browser tab, no extra tooling needed.

---

## How does it work?

```
You <-> Claude Code <-> Luthien <-> Anthropic API
                          |
                   logs every request and response
                   enforces the rules you define
                          |
                          |-- did it do what I asked?
                          |-- did it follow CLAUDE.md?
                          +-- did it do something suspicious?
```

Luthien sits in line as a transparent proxy. Every request and response flows through it, adding roughly 5-15ms of overhead. You define rules in YAML or Python, and Luthien enforces them on every request. It can call a separate "judge" model (like Claude Haiku) to evaluate responses in parallel, so enforcement does not block your workflow.

---

## Quick Start

Requires [Docker](https://www.docker.com/products/docker-desktop/):

```bash
curl -fsSL https://raw.githubusercontent.com/LuthienResearch/luthien-proxy/main/scripts/install.sh | bash
```

This installs [`uv`](https://docs.astral.sh/uv/) (if needed) and the Luthien CLI, downloads the proxy, walks you through configuration, starts the stack, and launches Claude Code through Luthien. Works with both API keys and Claude Pro/Max subscriptions.

After setup, use the CLI to manage the proxy:

```bash
luthien claude          # launch Claude Code through the proxy
luthien status          # check gateway health
luthien up / luthien down  # start/stop the stack
```

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

## Usage Telemetry

Luthien collects anonymous, aggregate usage metrics (request counts, token counts) to help improve the project. **No model names, API keys, IP addresses, or request/response content is collected.**

Telemetry is enabled by default and can be disabled:

```bash
# In .env or environment
USAGE_TELEMETRY=false
```

Or at runtime via the admin API: `PUT /api/admin/telemetry` with `{"enabled": false}`.

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
# Upstream LLM Provider API Keys (at least one required, or use Claude Pro/Max OAuth)
OPENAI_API_KEY=your_openai_api_key_here       # optional — needed for OpenAI-format policies
ANTHROPIC_API_KEY=your_anthropic_api_key_here  # optional if using Claude Pro/Max OAuth

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
    probability_threshold: 0.6  # block if judge confidence >= 60% (higher = more permissive)
    temperature: 0.0
    max_tokens: 256
```

Available policies in `src/luthien_proxy/policies/`:

- `noop_policy.py` - Pass-through (no filtering, default)
- `simple_policy.py` - Base class for custom request/response policies
- `simple_llm_policy.py` - Base class for policies using an LLM judge
- `tool_call_judge_policy.py` - AI-based tool call safety evaluation
- `string_replacement_policy.py` - Fast string find-and-replace on responses
- `all_caps_policy.py` - Simple transformation example
- `debug_logging_policy.py` - Logs requests/responses for debugging

## Dev Tooling

- Lint/format: `uv run ruff check` and `uv run ruff format`. Core rules enabled (E/F/I/D). Line length is 120; long-line lint (E501) is ignored to avoid churn after formatting.

Editor setup (VS Code)
- Install the Ruff extension.
- In this repo, VS Code uses Ruff for both formatting and import organization via `.vscode/settings.json`.
- Type checking: `uv run pyright` (configured in `[tool.pyright]` within `pyproject.toml`).
- Tests: `uv run pytest -q` with coverage for `src/luthien_proxy/**` configured in `[tool.pytest.ini_options]`.
- Config consolidation: Ruff, Pytest, and Pyright live in `pyproject.toml` to avoid extra files.

## Releasing

Releases are automated via GitHub Actions. The workflow:

1. **During development**: Add entries under `## Unreleased` in `CHANGELOG.md` as features land. A CI check posts a one-time reminder on PRs that don't update the changelog (skip with the `skip-changelog` or `chore` label).
2. **To release**: Rename `## Unreleased` to `## vX.Y.Z` in `CHANGELOG.md`, commit, then push a tag:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
3. **Automated**: The release workflow builds the package with `uv build` and creates a GitHub Release with the changelog notes and dist artifacts.

## architecture

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
  - `/api/activity/stream` - SSE activity stream
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

- `POST /v1/chat/completions` -OpenAI Chat Completions API (streaming and non-streaming)
- `POST /v1/messages` -Anthropic Messages API (streaming and non-streaming)
- `GET /health` -Health check

**UI Endpoints:**

- `GET /activity/monitor` -Real-time activity monitor (HTML)
- `GET /api/activity/stream` -SSE activity stream (JSON)
- `GET /debug` -Debug information viewer

**Authentication:**

All API requests require the `Authorization: Bearer <PROXY_API_KEY>` header.

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
      "model": "openai/gpt-4o-mini",
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
2. **Check upstream credentials**:
   - *API key mode*: Verify `ANTHROPIC_API_KEY` starts with `sk-ant-api` in `.env`
   - *Claude Max/OAuth mode*: Run `claude auth login` to ensure your session is active
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
docker compose run --rm migrations
```

## License

Apache License 2.0
