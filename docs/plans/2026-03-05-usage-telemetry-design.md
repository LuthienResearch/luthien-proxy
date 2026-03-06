# Usage Telemetry Design

**Date:** 2026-03-05
**Status:** Approved

## Goal

Track aggregate, non-identifiable usage across all luthien-proxy deployments for internal product analytics (Luthien team).

## Architecture

Each proxy instance maintains in-memory counters. Every 5 minutes, counters are rolled up into a JSON payload and POSTed to `https://telemetry.luthien.io/v1/events`. The domain is a CNAME controlled by Luthien, allowing backend swaps (PostHog, Datadog, custom Lambda, etc.) without any client-side changes.

Wire format is simple JSON over HTTPS. No OTLP, no SDK dependencies. Just `httpx.post()`.

## Rollup Payload

```json
{
  "schema_version": 1,
  "deployment_id": "a1b2c3d4-...",
  "proxy_version": "0.1.0",
  "python_version": "3.13.1",
  "interval_seconds": 300,
  "timestamp": "2026-03-05T17:00:00Z",
  "metrics": {
    "requests_accepted": 50,
    "requests_completed": 47,
    "input_tokens": 125000,
    "output_tokens": 38000,
    "streaming_requests": 40,
    "non_streaming_requests": 7,
    "sessions_with_ids": 3
  }
}
```

### Metric definitions

- `requests_accepted`: Incremented when a request enters the pipeline (both OpenAI and Anthropic paths)
- `requests_completed`: Incremented when a request completes successfully. For streaming, counted in the `finally` block where `final_status` is known.
- `input_tokens` / `output_tokens`: Anthropic path only. OpenAI/LiteLLM streaming doesn't reliably report usage, so it's excluded rather than undercounting silently.
- `streaming_requests` / `non_streaming_requests`: Count by request type
- `sessions_with_ids`: Distinct session IDs observed in the interval. Not all requests have session IDs, so this is approximate.

### Explicitly excluded

Model names, API keys, IP addresses, session content, policy configs, user identifiers, request/response payloads.

### Future addition

`policy_actions_taken` once the policy action concept is formalized.

## Opt-out

Three-layer precedence: **env var > DB value > first-run prompt**

1. **`USAGE_TELEMETRY` env var** (`true`/`false`) — highest priority, cannot be overridden by DB or UI
2. **DB-stored value** — set via admin UI toggle, persisted across container restarts
3. **First-run prompt in `quick_start.sh`** — if neither env var nor DB value exists, prompt: "Send anonymous usage data? [Y/n]", store result in DB

**Default behavior when neither env nor DB value exists** (Docker, standalone, non-interactive starts): **enabled**. This is standard opt-out telemetry. The prompt in `quick_start.sh` is a UX nicety, not the only path.

Note: this precedence is the **opposite** of the existing auth config pattern (where DB overrides env). The distinction must be clear in code and docs to avoid copy-paste confusion.

## Implementation Components

### New module: `src/luthien_proxy/usage_telemetry/`

- `collector.py` — In-memory counters with atomic increment methods and rollup-and-reset. Uses a set for `sessions_with_ids` (cleared each interval).
- `sender.py` — Periodic async task (5 min interval). `httpx.post()` to endpoint. Graceful failure handling (log + discard, no retry storm). Flush final interval on shutdown.
- `config.py` — Reads opt-out setting (env > DB > default enabled). Generates and persists `deployment_id` (random UUID, stored in DB on first run).

### Settings changes

- `USAGE_TELEMETRY` env var in `Settings` class
- `TELEMETRY_ENDPOINT` env var (defaults to `https://telemetry.luthien.io/v1/events`)

### DB migration

New single-row table `telemetry_config` (mirrors `auth_config` pattern):
- `enabled` (boolean, nullable — null means "use default")
- `deployment_id` (UUID, generated on first startup)

### Integration points

- **Counter increments:**
  - `requests_accepted`: at pipeline entry in `processor.py` and `anthropic_processor.py`
  - `requests_completed`, `streaming_requests`, `non_streaming_requests`: in the `finally` blocks of the streaming generators and non-streaming handlers where `final_status` is known
  - `input_tokens`, `output_tokens`: Anthropic path only, from usage data on the response
  - `sessions_with_ids`: when session ID is extracted (both paths)
- **Sender lifecycle:** started as background task in `lifespan()` in `main.py`, cancelled and flushed on shutdown
- **Collector injection:** via `Dependencies` container, not globals

### Admin API

- `GET /api/admin/telemetry` — returns current config (enabled, deployment_id)
- `PUT /api/admin/telemetry` — update enabled status (writes to DB)

### Admin UI

Toggle on an **admin-protected page** (not the public landing page). Add to `/credentials` page or a new `/settings` page.

### `quick_start.sh`

After services are up and DB is ready, check if `USAGE_TELEMETRY` env var is set. If not, query the admin API for stored config. If no stored config, prompt and store via admin API.

### `.env.example`

```bash
# Anonymous usage telemetry (opt-out: set to false to disable)
# USAGE_TELEMETRY=true
# TELEMETRY_ENDPOINT=https://telemetry.luthien.io/v1/events
```

### Version source of truth

Read `proxy_version` from `importlib.metadata.version("luthien-proxy")` to use `pyproject.toml` as the single source.

## Privacy notes

- Counting happens from typed fields at integration points, never by consuming existing event payloads (which contain full request/response bodies)
- `deployment_id` is a random UUID with no connection to any user identity
- `sessions_with_ids` is a count, not a list of session IDs

## What this does NOT include

- No sink abstraction — just `httpx.post()`. Add later if needed.
- No per-request events — rollups only.
- No policy action counting — pending formalization.
- No OpenAI/LiteLLM token counting — unreliable on streaming paths.
