# Overseer Test Harness

Multi-turn end-to-end testing of the Luthien proxy gateway using Claude Code in a Docker sandbox.

## Prerequisites

1. **`.env` file** in the project root with at least:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
   The overseer LLM client reads this key to analyze each turn.

2. **Docker Compose stack running** with the `overseer` profile:
   ```bash
   docker compose --profile overseer up -d
   ```
   This starts the gateway, database, redis, and sandbox containers.

3. **Ports**: The gateway runs on `:8000` (inside Docker network). The report dashboard defaults to `:8080` on the host (configurable with `--port`).

## Quick Start

```bash
# 1. Start the stack
docker compose --profile overseer up -d

# 2. Run a session
uv run python -m scripts.overseer.main --task "Build a hello world Flask app"
```

Open `http://localhost:8080` to see the live dashboard.

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--task` | *(required)* | Initial prompt for Claude Code |
| `--max-turns` | 20 | Stop after N turns |
| `--timeout` | max_turns * idle_timeout * 10 | Global session timeout (seconds) |
| `--idle-timeout` | 180 | Kill turn after N seconds of no stdout output |
| `--port` | 8080 | Report dashboard port |
| `--model` | claude-haiku-4-5-20251001 | Overseer analysis model |
| `--sandbox-model` | claude-haiku-4-5-20251001 | Model for Claude Code in sandbox |
| `--gateway-url` | http://gateway:8000 | Proxy URL from container perspective |
| `--compose-project` | from env | Docker Compose project name |

## How It Works

1. The **session driver** sends prompts to Claude Code running inside the sandbox container.
2. Each turn's raw stream-json output is parsed by the **stream parser** into structured events.
3. The **overseer LLM** analyzes each turn for anomalies (policy violations, unexpected behavior).
4. Results are served on a live **report dashboard**.
