# Overseer: Multi-Turn E2E Proxy Testing

## Goal

Find proxy/gateway bugs (streaming drops, session corruption, event loss, /compact failures) by running extended, open-ended Claude Code sessions through the proxy and monitoring for anomalies.

## Architecture

```
Overseer Script (host)
├── Overseer LLM (direct Anthropic API, Haiku)
│   Analyzes turn output, detects soft anomalies, generates next prompt
├── Session Driver
│   docker exec → claude -p --resume $SID inside sandbox container
│   Parses stream-json, rule-based anomaly detection
└── Report Server (aiohttp, SSE)
    Live-updating HTML dashboard at localhost:8080

Sandbox Container (luthien-sandbox)
├── node:22-slim + claude CLI + git + python3
├── Network: reaches gateway via docker network
├── /work: tmpfs working directory
└── No baked-in API keys — passed at runtime

Luthien Proxy (gateway:8000)
└── The system under test
```

## Components

### 1. Docker Sandbox (`docker/sandbox/Dockerfile`)

- Base: `node:22-slim`
- Installs: `@anthropic-ai/claude-code` (global npm), git, python3, build-essential
- Entrypoint: `sleep infinity` (overseer does `docker exec`)
- `/work` directory for Claude Code to operate in
- API keys passed via env vars at exec time
- Added to docker-compose (or separate `docker-compose.overseer.yaml`)
- Shares network with gateway service

### 2. Overseer Script (`scripts/overseer.py`)

CLI interface:
```bash
python scripts/overseer.py \
  --task "Build a small Python CLI calculator with tests" \
  --max-turns 20 \
  --timeout 600 \
  --port 8080 \
  --model haiku
```

Core loop:
1. Start/verify sandbox container
2. Start report HTTP server
3. First turn: `claude -p "$task" --output-format stream-json --verbose --dangerously-skip-permissions`
4. Capture `session_id` from init event
5. Loop:
   - Parse stream-json events
   - Update session state (turns, tools, errors, cost, latency)
   - Rule-based anomaly detection
   - Feed turn summary to overseer LLM for soft analysis + next prompt
   - Update HTML report via SSE
   - `claude -p "$next_prompt" --resume $SID ...`
6. Generate final summary
7. Stop sandbox container

### 3. Anomaly Detection

**Rule-based (always runs):**
- Non-zero exit code from `claude` process
- `is_error: true` in result events
- Turn latency > 60s (configurable threshold)
- Tool call with no corresponding tool result
- Session ID changed unexpectedly
- Cost spike (> 2x average turn cost)

**LLM-based (overseer adds soft signals):**
- Response seems truncated
- Claude seems confused about prior context
- Unexpected behavioral shifts

### 4. Report Server

- `aiohttp` server on configurable port
- SSE push for live updates (no polling)
- Dashboard shows: turn count, tool usage frequency, errors/anomalies (highlighted), cumulative cost, per-turn latency, current status
- Self-contained HTML (inline CSS/JS)

## Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--task` | (required) | Initial task prompt for the Claude Code session |
| `--max-turns` | 20 | Stop after N turns |
| `--timeout` | 600 | Stop after N seconds |
| `--port` | 8080 | Report server port |
| `--model` | haiku | Overseer LLM model |
| `--gateway-url` | http://gateway:8000 | Proxy URL (from container's perspective) |
| `--api-key` | from env | API key for proxy |

## Out of Scope (for now)

- Concurrent session testing
- Policy-specific test scenarios
- CI integration
- Historical trend tracking
- Automated remediation
