# Luthien CLI

A standalone CLI tool for managing and interacting with [luthien-proxy](https://github.com/LuthienResearch/luthien-proxy) gateways.

## Install

```bash
pipx install luthien-cli
# or for development:
pip install -e ".[dev]"
```

## Quick Start

```bash
# Configure your gateway
luthien config set gateway.url http://localhost:8000
luthien config set gateway.admin_key admin-your-key

# Check gateway status
luthien status

# Launch Claude Code through the proxy (passthrough auth by default)
luthien claude
luthien claude -- --model opus

# Optional: use a proxy API key instead of passthrough auth
luthien config set gateway.api_key sk-your-proxy-key

# Manage local stack (requires repo_path)
luthien config set local.repo_path /path/to/luthien-proxy
luthien up
luthien logs -f
luthien down
```

## Commands

| Command | Description |
|---------|-------------|
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
| `gateway.api_key` | Optional proxy API key — sent as `ANTHROPIC_API_KEY` to the gateway when set (server-side key mode). Omit for passthrough auth (default). |
| `gateway.admin_key` | Admin API key — sent as `Authorization: Bearer <key>` for admin endpoints (`luthien status`) |
| `local.repo_path` | Path to luthien-proxy repo checkout (for `up`/`down`/`logs`) |
