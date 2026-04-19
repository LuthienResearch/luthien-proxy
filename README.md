# Luthien <!-- README v11.0 -->

### Claude Code builds. You stay in control.

```bash
curl -fsSL https://luthien.cc/install.sh | bash
```

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/luthien-proxy-oneclick)

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

No Docker required. The install command at the top of this page installs [`uv`](https://docs.astral.sh/uv/) (if needed) and the Luthien CLI, sets up the gateway with SQLite, walks you through configuration, and starts the proxy.

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

Copy `.env.example` to `.env` and edit as needed. Most fields are optional — the defaults written by `luthien onboard` are enough for single-user local development.

### Authentication

From a client's point of view, Luthien is just an Anthropic endpoint. Point your client at `http://localhost:8000` (set `ANTHROPIC_BASE_URL`) and use your normal `ANTHROPIC_API_KEY` or Claude Pro/Max OAuth session — no Luthien-specific key, header, or flow. This is **passthrough**: the gateway forwards your credentials upstream to Anthropic and bills against your own account.

The gateway has two admin-side keys — both optional for single-user local development:

| Env var | What it controls | When to set it |
|---|---|---|
| `ADMIN_API_KEY` | Admin dashboard + admin API (History, Policy Config, `/api/admin/*`). Localhost bypass applies by default (`LOCALHOST_AUTH_BYPASS=true`). | Set automatically by `luthien onboard`. Required for remote admin access. |
| `CLIENT_API_KEY` | A shared value the gateway will also accept as a client credential. When a client sends exactly this value as its `ANTHROPIC_API_KEY`, the gateway forwards the request using the server's own `ANTHROPIC_API_KEY` instead. Useful for operators who don't want to distribute their real Anthropic key. | Only if you want a single shared key that multiple machines can use without knowing the real Anthropic credential. If unset, clients simply use their own Anthropic credentials (the default passthrough path). |

> :warning: **Server-side `ANTHROPIC_API_KEY` bills per token.** It is only consulted for requests that match `CLIENT_API_KEY`. Claude Pro/Max subscribers should leave both unset and rely on OAuth passthrough.

#### What you get by default

After `luthien onboard`, the gateway runs with `AUTH_MODE=both`, `ADMIN_API_KEY` set, and neither `CLIENT_API_KEY` nor a server-side `ANTHROPIC_API_KEY` set. In this setup:

1. Clients pass their own Anthropic OAuth token or API key (as `ANTHROPIC_API_KEY`) and it is forwarded upstream — **passthrough**, no gateway-specific credential needed.
2. The admin dashboard is reachable without the admin key from localhost. Remote access requires `Authorization: Bearer <ADMIN_API_KEY>`.

Add `CLIENT_API_KEY` + server-side `ANTHROPIC_API_KEY` only if you want a shared, rotatable credential that hides the real Anthropic key from clients.

> **Source-clone note**: `./scripts/start_gateway.sh` populates `.env` from `.env.local.example`, which seeds a dev `CLIENT_API_KEY=sk-local-dev` for convenience, and the script itself defaults `CLIENT_API_KEY` to `sk-luthien-dev-key` if unset. The source-clone path is meant for gateway development, not end users — use `luthien onboard` for that.

#### Configuring keys manually

```bash
# Required for remote admin dashboard access; auto-generated by `luthien onboard`
ADMIN_API_KEY=admin-dev-key

# Optional — clients that set ANTHROPIC_API_KEY to this value will be accepted
CLIENT_API_KEY=sk-luthien-dev-key

# Required only if CLIENT_API_KEY is set — used to forward matching requests upstream
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

For the full auth architecture (auth modes, OAuth passthrough details, judge key resolution), see [`dev/context/authentication.md`](dev/context/authentication.md).

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

1. **Check client credentials (default setup)**: Luthien's default mode is passthrough — clients forward their own Anthropic credentials upstream. For Claude Code, run `claude auth login` to ensure your Claude Pro/Max session is active. No Luthien-specific credential is required.
2. **Only if you explicitly set `CLIENT_API_KEY`**: Clients authenticate by putting that exact value in their `ANTHROPIC_API_KEY` env var (sent as `x-api-key` or `Authorization: Bearer`). The gateway then forwards the request using the server-side `ANTHROPIC_API_KEY`, so that must also be set (otherwise the gateway returns 500).
3. **Check logs**: `luthien logs` (local mode) or `docker compose logs -f gateway` (Docker mode).
4. **Dashboard login page appearing on localhost?** `LOCALHOST_AUTH_BYPASS` is normally on — if you see a login page, the bypass may have been disabled. Check `/config` or `.env` for `LOCALHOST_AUTH_BYPASS=true`, and ensure you're actually hitting the gateway from `127.0.0.1` / `::1`.

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
