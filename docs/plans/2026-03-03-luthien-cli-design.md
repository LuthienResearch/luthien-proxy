# Luthien CLI Design

## Overview

A standalone, pipx-installable CLI tool (`luthien`) for managing and interacting with luthien-proxy gateways. Acts as a client-side tool that talks to gateways over HTTP, with optional local docker-compose stack management.

## Package

- **Name**: `luthien-cli`
- **Command**: `luthien`
- **Install**: `pipx install luthien-cli`
- **Location**: `luthien-cli/` directory in the luthien-proxy repo (own pyproject.toml)

### Dependencies (minimal)

- `click` — CLI framework
- `httpx` — HTTP client for gateway API calls
- `tomli` / `tomli-w` — config file read/write (TOML)
- `rich` — terminal output formatting

### Structure

```
luthien-cli/
├── pyproject.toml
├── src/luthien_cli/
│   ├── __init__.py
│   ├── main.py            # click group, entry point
│   ├── config.py           # ~/.luthien/ config management
│   ├── gateway_client.py   # httpx wrapper for gateway APIs
│   └── commands/
│       ├── up.py           # luthien up/down
│       ├── claude.py       # luthien claude [args...]
│       ├── status.py       # luthien status
│       ├── logs.py         # luthien logs
│       └── config_cmd.py   # luthien config
└── tests/
```

## Config (`~/.luthien/config.toml`)

```toml
[gateway]
url = "http://localhost:8000"
api_key = "your-proxy-api-key"
admin_key = "your-admin-api-key"

[local]
repo_path = "/home/jai/projects/luthien-proxy"
```

- First run prompts for gateway URL (default localhost:8000) and API key
- `--gateway-url` and `--api-key` flags override config per-invocation
- `local.repo_path` only needed for `luthien up/down`
- Single gateway config (no profiles in v1)

## Commands

### `luthien up`

Starts local docker-compose stack (db, redis, gateway).

- Requires `local.repo_path` in config (prompts if missing)
- Runs `docker compose up -d` in the repo directory
- Waits for `/health` to return healthy
- Shows gateway URL on success
- `--follow` flag to tail logs after startup

### `luthien down`

Stops local stack via `docker compose down`.

### `luthien claude [args...]`

Launches Claude Code pointed at the configured gateway.

- Checks gateway health first
- Sets `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` from config
- `exec`s `claude` with all passthrough args
- Errors with suggestion to `luthien up` if gateway unhealthy

### `luthien status`

Displays gateway state:

- Health status (`/health`)
- Active policy (`/api/admin/policy/current`)
- Auth mode
- Gateway URL

Formatted as a compact rich table.

### `luthien logs`

- Local: `docker compose logs -f gateway`
- Remote: `/api/debug/calls` recent entries
- `--tail N` for line/entry count
- `--follow` for streaming (local only)

### `luthien config`

- `luthien config show` — print current config
- `luthien config set <key> <value>` — update a value
