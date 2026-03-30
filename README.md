# Luthien <!-- README v11.0 -->

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

```bash
curl -fsSL https://raw.githubusercontent.com/LuthienResearch/luthien-proxy/main/scripts/install.sh | bash
```

No Docker required. This installs [`uv`](https://docs.astral.sh/uv/) (if needed) and the Luthien CLI, sets up the gateway with SQLite, walks you through configuration, and starts the proxy. Works with both API keys and Claude Pro/Max subscriptions.

> **Platform support**: Linux and macOS. Windows is not currently supported.

After setup, use the CLI or Claude Code to manage the proxy:

| CLI command | Claude Code | What it does |
|---|---|---|
| `luthien claude` | — | Launch Claude Code through the proxy |
| `luthien status` | `!luthien status` | Check gateway health |
| `luthien up` | `!luthien up` | Start the gateway |
| `luthien down` | `!luthien down` | Stop the gateway |
| `luthien logs` | `!luthien logs` | View gateway logs |

> **Docker mode**: If you prefer PostgreSQL + Redis, run `luthien onboard --docker` instead. Requires [Docker](https://www.docker.com/products/docker-desktop/).

> **Next step**: Once the gateway is running, see [Configuration](#configuration) to set up API keys and customize policies.

---

## What You Get

- **Gateway** (Anthropic-compatible) at <http://localhost:8000>
- **SQLite** storage (zero setup) — or PostgreSQL + Redis with `--docker`
- **Real-time monitoring** at <http://localhost:8000/activity/monitor>
- **Policy management UI** at <http://localhost:8000/policy-config>

> **Trouble accessing the dashboard?** The monitoring and policy UIs require the admin API key. On localhost, auth is bypassed by default — but if you're accessing from another host or see a login page, see [Configuration](#configuration) below.

## Configuration

Copy `.env.example` to `.env` and configure your environment:

### Required Configuration

```bash
# Upstream LLM Provider API Keys (at least one required, or use Claude Pro/Max OAuth)
OPENAI_API_KEY=your_openai_api_key_here       # optional — needed for OpenAI-format policies
ANTHROPIC_API_KEY=your_anthropic_api_key_here  # optional if using Claude Pro/Max OAuth

# Gateway Authentication
PROXY_API_KEY=sk-luthien-dev-key     # API key for clients to access the proxy
ADMIN_API_KEY=admin-dev-key          # API key for admin/policy management UI (History, Policy tabs)
```

> **Two API keys, two purposes**: `PROXY_API_KEY` is for Claude Code and other LLM clients connecting through the gateway. `ADMIN_API_KEY` is for the web dashboard (History, Policy Configuration, Activity Monitor). On localhost, the dashboard bypasses auth automatically.

### Core Infrastructure

```bash
# Database — leave unset for SQLite (default: ~/.luthien/local.db)
# For Docker Compose / multi-user deployments, use PostgreSQL + Redis:
# DATABASE_URL=postgresql://luthien:password@db:5432/luthien_control
# REDIS_URL=redis://redis:6379

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
POLICY_CONFIG=./config/policy_config.yaml
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

### Built-in Presets

Ready-to-use policies in `src/luthien_proxy/policies/presets/` — no configuration needed:

- `BlockDangerousCommandsPolicy` - Blocks destructive shell commands (rm -rf, chmod 777, mkfs, dd, etc.)
- `BlockSensitiveFileWritesPolicy` - Blocks writes to sensitive paths (/etc, ~/.ssh, ~/.gnupg, etc.)
- `BlockWebRequestsPolicy` - Blocks outbound network requests (curl, wget, fetch, etc.) to prevent data exfiltration
- `NoApologiesPolicy` - Removes apologetic filler ("I apologize", "I'm sorry") from responses
- `NoYappingPolicy` - Enforces concise responses by cutting filler, hedging, and unnecessary preamble
- `PlainDashesPolicy` - Replaces em-dashes and en-dashes with plain hyphens (useful for terminals)
- `PreferUvPolicy` - Replaces pip commands with uv equivalents in responses

Example preset config:

```yaml
policy:
  class: "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy"
  config: {}
```

### Core Policies

Base classes and building blocks in `src/luthien_proxy/policies/` — see **[docs/policies.md](docs/policies.md)** for full reference with examples:

**Quick Start Presets** (zero config):
- `NoYappingPolicy` - Remove filler and hedging
- `NoApologiesPolicy` - Strip apologetic language
- `PlainDashesPolicy` - Replace Unicode dashes with hyphens
- `PreferUvPolicy` - Replace pip commands with uv equivalents
- `BlockDangerousCommandsPolicy` - Block rm -rf, chmod 777, etc.
- `BlockWebRequestsPolicy` - Block curl, wget, network requests
- `BlockSensitiveFileWritesPolicy` - Block writes to /etc, ~/.ssh, etc.

**Core Policies** (configurable):
- `NoOpPolicy` - Pass-through (default)
- `SimpleLLMPolicy` - Apply plain-English instructions via judge LLM
- `ToolCallJudgePolicy` - Probability-based tool call blocking
- `StringReplacementPolicy` - Fast string find-and-replace
- `AllCapsPolicy` - Simple transformation example
- `DebugLoggingPolicy` - Log requests/responses for debugging

**Composition**:
- `MultiSerialPolicy` - Chain policies sequentially

## Usage Telemetry

Luthien collects anonymous, aggregate usage metrics to help track adoption and improve the project. **No model names, API keys, IP addresses, or request/response content is collected.**

Metrics collected every 5 minutes: request counts, token counts (input/output), streaming vs non-streaming breakdown, and active session count. Data is sent to `telemetry.luthien.cc` (a Cloudflare Worker) and stored in Grafana Cloud.

Telemetry is enabled by default and can be disabled:

```bash
# In .env or environment
USAGE_TELEMETRY=false
```

Or at runtime via the admin API: `PUT /api/admin/telemetry` with `{"enabled": false}`.

## Troubleshooting

### Gateway not starting

```bash
# Local mode (default)
luthien status          # check health
luthien logs            # view gateway logs
luthien down && luthien up  # full restart

# Docker Compose mode
docker compose ps
docker compose logs gateway
docker compose down && ./scripts/quick_start.sh
```

### API requests failing

1. **Check API key**: Ensure `Authorization: Bearer <PROXY_API_KEY>` header is set
2. **Check upstream credentials**:
   - *API key mode*: Verify `ANTHROPIC_API_KEY` starts with `sk-ant-api` in `.env`
   - *Claude Max/OAuth mode*: Run `claude auth login` to ensure your session is active
3. **Check logs**: `luthien logs` (local mode) or `docker compose logs -f gateway` (Docker mode)

### Database connection issues

Local mode uses SQLite — if the database file is corrupt, delete it and restart (`rm ~/.luthien/local.db && luthien up`).

For Docker Compose deployments:
```bash
docker compose ps db
docker compose restart db
docker compose run --rm migrations
```

## Development

For development setup, tooling, architecture, and API details, see **[dev-README.md](dev-README.md)**.

## License

Apache License 2.0
