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

### Enforce arbitrary policies

- **Block dangerous operations** — `rm -rf`, `git push --force`, dropping database tables
- **Enforce package standards** — block `pip install`, suggest `uv add` instead
- **Clean up AI writing tics** — remove em dashes, curly quotes, over-bulleting
- **Enforce scope boundaries** — only allow changes to files mentioned in the request

**Example: ToolCallJudgePolicy** — an LLM judge that evaluates every tool call:

```yaml
# config/policy_config.yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    model: "anthropic/claude-haiku-4-5-20251001"  # swap for a larger model if needed
    probability_threshold: 0.6  # block when judge LLM's subjective risk score >= 0.6 (higher = more permissive)
    judge_instructions: >
      Block any 'pip install' commands. Suggest 'uv add' instead.
      Block 'rm -rf' or any recursive delete on project directories.
      Block 'git push --force' to main or master.
```

> The `class:` field is a Python import path (`module:ClassName`). You can use any of the [built-in policies](#core-policies) or write your own.

### Log everything passing through the proxy

Every request and response between Claude Code and the Anthropic API is recorded automatically.

- **Live conversation view** - open [localhost:8000/history](http://localhost:8000/history) to see your full agent conversation in a readable format, including live streaming at `/conversation/live/{id}`
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
curl -fsSL https://luthien.cc/install.sh | bash
```

No Docker required. This installs [`uv`](https://docs.astral.sh/uv/) (if needed) and the Luthien CLI, sets up the gateway with SQLite, walks you through configuration, and starts the proxy.

> **Claude Pro/Max users**: You don't need an API key. Luthien passes your existing Claude subscription credentials through to the Anthropic API — no extra cost, no configuration needed.

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
- **Conversation history** at <http://localhost:8000/history> with live view at `/conversation/live/{id}`
- **Policy management UI** at <http://localhost:8000/policy-config>

> **Trouble accessing the dashboard?** The monitoring and policy UIs require the admin API key. On localhost, auth is bypassed by default — but if you're accessing from another host or see a login page, see [Configuration](#configuration) below.

## Configuration

Copy `.env.example` to `.env` and configure your environment:

### Authentication & Billing

Luthien supports two ways to authenticate with the Anthropic API:

| Mode | Who pays | Setup |
|------|----------|-------|
| **OAuth passthrough** (default) | Your existing Claude Pro/Max subscription | Nothing — just run `luthien claude` |
| **API key** | Per-token billing to your Anthropic API account | Set `ANTHROPIC_API_KEY` in `.env` |

> :warning: **API key mode bills per token.** If you set `ANTHROPIC_API_KEY` in your `.env`, all requests through the proxy are billed to that API key at [Anthropic's per-token rates](https://docs.anthropic.com/en/docs/about-claude/models). This can result in significant charges. If you have a Claude Pro or Max subscription, you don't need an API key — OAuth passthrough is the default and uses your existing subscription at no extra cost.

### Gateway Keys

```bash
# Gateway Authentication
PROXY_API_KEY=sk-luthien-dev-key     # API key for clients to access the proxy
ADMIN_API_KEY=admin-dev-key          # API key for admin/policy management UI (History, Policy tabs)
```

> **Two gateway keys, two purposes**: `PROXY_API_KEY` is for Claude Code and other LLM clients connecting through the gateway. `ADMIN_API_KEY` is for the web dashboard (History, Policy Configuration, Activity Monitor). On localhost, the dashboard bypasses auth automatically.

### Upstream API Keys (Optional)

```bash
# Only needed if NOT using Claude Pro/Max OAuth passthrough
ANTHROPIC_API_KEY=your_anthropic_api_key_here  # optional — per-token billing, see warning above
```

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
LLM_JUDGE_MODEL=anthropic/claude-haiku-4-5-20251001   # Model for judge
LLM_JUDGE_API_KEY=your_judge_api_key                 # optional — only if judge needs a different key than the client's
```

See `.env.example` for all available options and defaults.

### Policy File Format

The gateway loads policies from `POLICY_CONFIG` (defaults to `config/policy_config.yaml`).

Example policy configuration:

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    model: "anthropic/claude-haiku-4-5-20251001"  # swap for a larger model if needed
    probability_threshold: 0.6  # block when judge LLM's subjective risk score >= 0.6 (higher = more permissive)
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

**Configurable policies**:
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

`luthien onboard` sets `AUTH_MODE=both` and does **not** set a `PROXY_API_KEY` — clients
authenticate by forwarding their upstream credential (Claude Pro/Max OAuth session or
`ANTHROPIC_API_KEY`). Start with the credential path:

1. **Check upstream credentials**:
   - *OAuth passthrough (default)*: Run `claude auth login` to ensure your Claude Pro/Max session is active
   - *API key mode*: Verify `ANTHROPIC_API_KEY` starts with `sk-ant-api` in `.env`
2. **Check logs**: `luthien logs` (local mode) or `docker compose logs -f gateway` (Docker mode)
3. **Only if you explicitly configured `PROXY_API_KEY`**: ensure clients send it via
   `Authorization: Bearer <PROXY_API_KEY>` or `x-api-key: <PROXY_API_KEY>`. Onboarding does
   not set this, so skip this step unless you added one manually.

### Database connection issues

Local mode uses SQLite — if the database file is corrupt, delete it and restart (`rm ~/.luthien/local.db && luthien up`).

For Docker Compose deployments:
```bash
docker compose ps db
docker compose restart db
docker compose run --rm migrations
```

## Uninstall

**Local mode** (default):

```bash
luthien down
uv tool uninstall luthien-cli
rm -rf ~/.luthien  # removes all conversation logs, database, and config
```

**Docker Compose mode**:

```bash
docker compose down -v  # -v also removes the persistent database volume
uv tool uninstall luthien-cli
rm -rf ~/.luthien  # removes all conversation logs and config
```

## Development

### Quick Start (from source, no Docker)

Clone the repo and start the gateway with SQLite — no Postgres or Redis needed:

```bash
git clone https://github.com/LuthienResearch/luthien-proxy.git
cd luthien-proxy
uv sync  # Install uv first if needed: https://docs.astral.sh/uv/getting-started/installation/
./scripts/start_gateway.sh
```

To use API key auth, edit `.env` (auto-created on first run) and add your `ANTHROPIC_API_KEY`.

The gateway starts at `http://localhost:8000`. For full development setup, tooling, architecture, releasing, and API details, see **[dev-README.md](dev-README.md)**.

## License

Apache License 2.0
