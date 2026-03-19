# Docker-Free Local Mode

## Summary

Make `luthien onboard` default to running the gateway as a native Python process
with SQLite and in-process event publishing, eliminating Docker as a requirement
for getting started. Docker remains available via `--docker` flag.

## Architecture

### Local mode (new default)

```
install.sh → uv + luthien-cli → luthien onboard
  → install luthien-proxy into ~/.luthien/venv/
  → generate keys, prompt for policy
  → write .env (DATABASE_URL=sqlite, no REDIS_URL)
  → start gateway as background process
  → write PID file, log file
  → health check → show results
```

### Process management

- `luthien up`: Start `~/.luthien/venv/bin/python -m luthien_proxy.main` as
  detached subprocess. Write PID to `~/.luthien/luthien-proxy/gateway.pid`,
  stdout/stderr to `gateway.log`.
- `luthien down`: Read PID file, send SIGTERM, clean up PID file.
- `luthien logs`: Tail `gateway.log`.

### Directory layout

```
~/.luthien/
  config.toml              # mode = "local", gateway_url, keys
  venv/                    # managed Python venv with luthien-proxy
  luthien-proxy/
    config/policy_config.yaml
    .env
    luthien.db             # SQLite (created by gateway at startup)
    gateway.pid
    gateway.log
```

### Environment for local mode

```env
DATABASE_URL=sqlite:///~/.luthien/luthien-proxy/luthien.db
PROXY_API_KEY=sk-luthien-...
ADMIN_API_KEY=admin-...
POLICY_SOURCE=file
POLICY_CONFIG=~/.luthien/luthien-proxy/config/policy_config.yaml
GATEWAY_PORT=8000
AUTH_MODE=both
```

No `REDIS_URL` → gateway auto-uses InProcessEventPublisher + InProcessCredentialCache.

## Changes

### `scripts/install.sh`
- Remove Docker check (lines 10-20)
- Keep uv install, luthien-cli install, `luthien onboard`

### `src/luthien_cli/src/luthien_cli/config.py`
- Add `mode: str = "local"` to `LuthienConfig`
- Persist in `config.toml` under `[local]`

### `src/luthien_cli/src/luthien_cli/repo.py`
- Add `ensure_gateway_venv()`: creates `~/.luthien/venv/`, installs luthien-proxy
- Add `update_gateway_venv()`: upgrades luthien-proxy in the venv
- Keep existing `ensure_repo()` for Docker mode

### `src/luthien_cli/src/luthien_cli/commands/onboard.py`
- Default to local mode
- Add `--docker` flag for Docker mode (preserves existing flow)
- Local mode flow:
  1. Call `ensure_gateway_venv()` to install gateway
  2. Generate keys, prompt for policy (unchanged)
  3. Write `.env` with SQLite DATABASE_URL, no REDIS_URL
  4. Start gateway as background process
  5. Health check, show results

### `src/luthien_cli/src/luthien_cli/commands/up.py`
- Extract process management into local_process module
- `up`: dispatch on config.mode — "local" starts process, "docker" runs compose
- `down`: dispatch on config.mode — "local" kills PID, "docker" runs compose down

### `src/luthien_cli/src/luthien_cli/local_process.py` (new)
- `start_gateway(repo_path, port_env)` — launch background process, write PID
- `stop_gateway(repo_path)` — read PID, SIGTERM, clean up
- `is_gateway_running(repo_path)` — check PID file + process alive

### `src/luthien_cli/src/luthien_cli/commands/logs.py`
- Support tailing `gateway.log` in local mode (currently Docker-only)

### `README.md`
- Quick Start: remove "Requires Docker"
- Mention Docker as alternative for production
- Update "What You Get" section

## Not in scope
- Automatic gateway restart on crash (systemd/launchd integration)
- Migration from Docker mode to local mode
- Windows support (PID/signal handling is Unix-specific)
