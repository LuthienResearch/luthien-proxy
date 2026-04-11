# Luthien CLI

A standalone CLI tool for managing and interacting with [luthien-proxy](https://github.com/LuthienResearch/luthien-proxy) gateways.

## Install

```bash
uv tool install luthien-cli
# or for development:
uv pip install -e ".[dev]"
```

## Quick Start

```bash
# Run the interactive setup (downloads proxy automatically)
luthien onboard

# Launch Claude Code through the proxy
luthien claude

# Check gateway status
luthien status

# Optional: manage the stack manually
luthien up
luthien logs -f
luthien down
```

## Commands

| Command | Description |
|---------|-------------|
| `luthien onboard` | Interactive setup — downloads proxy, configures policy, starts gateway |
| `luthien status` | Show gateway health, active policy, and auth mode |
| `luthien claude [args...]` | Launch Claude Code routed through the gateway |
| `luthien up [--follow]` | Start the local docker-compose stack |
| `luthien down` | Stop the local stack |
| `luthien logs [-f] [-n N]` | View gateway logs |
| `luthien config show` | Display current configuration |
| `luthien config set <key> <value>` | Update a config value |

## Configuration

Config is stored at `~/.luthien/config.toml`:

```toml
[gateway]
url = "http://localhost:8000"
api_key = "sk-your-proxy-key"
admin_key = "admin-your-key"

[local]
repo_path = "/path/to/luthien-proxy"
```

| Key | Description |
|-----|-------------|
| `gateway.url` | Gateway base URL (default: `http://localhost:8000`) |
| `gateway.api_key` | Stored but currently unused by `luthien claude` — the CLI only sets `ANTHROPIC_BASE_URL` and lets Claude Code forward its own credentials upstream. Reserved for future proxy-key injection; safe to leave unset. |
| `gateway.admin_key` | Admin API key — sent as `Authorization: Bearer <key>` for admin endpoints (`luthien status`) |
| `local.repo_path` | Auto-set by `luthien onboard`. Override to use a custom repo checkout. |
