# Sentry Error Tracking

How and why Sentry is integrated, what data it collects, and how to use it across environments.

**Added**: 2026-03-14
**Files**: `main.py:56-152`, `settings.py:73-77`, `tests/conftest.py:35`, `saas_infra/provisioner.py:97-98`

---

## Why Sentry

The proxy had zero error tracking (flagged as HIGH in the March 2026 audit). Errors in production were invisible unless someone checked logs. Sentry gives us:

- Automatic error capture with stack traces across all deployments
- Error grouping, deduplication, and alerting
- Environment/release tagging to distinguish dev, test, Railway, and self-hosted instances
- Error collection from alpha/beta users (opt-out, not opt-in) to catch issues we can't reproduce locally

## Consent Model: Opt-Out

Follows the same pattern as usage telemetry: **enabled by default**, users set `SENTRY_ENABLED=false` to opt out.

Why opt-out (not opt-in):
- The proxy is an infrastructure tool, not a consumer app — operators expect telemetry
- Matching the existing `USAGE_TELEMETRY` pattern (also opt-out) keeps behavior consistent
- Opt-in would mean zero error reports from most deployments, defeating the purpose

The DSN is hardcoded in `settings.py` as a default. This is safe — a Sentry DSN is a write-only ingest key, not a secret. Anyone who extracts it can only submit error reports, not read data. Sentry's spike protection and inbound filters mitigate abuse.

## Two-Layer Data Scrubbing

The proxy handles API keys and LLM traffic, so error reports must never leak credentials or prompt content. Scrubbing happens in two layers:

### Layer 1: EventScrubber (Sentry built-in)

Scrubs values whose **key names** match a denylist. Runs automatically before `before_send`.

Built-in denylist includes: `password`, `secret`, `api_key`, `apikey`, `auth`, `credentials`, `token`, `authorization`, `cookie`, etc.

We extend it with proxy-specific keys:
```
anthropic_api_key, openai_api_key, proxy_api_key, admin_api_key,
resolved_api_key, explicit_key, bearer_token, api_key_header
```

This catches API keys wherever they appear as local variables in stack frames — `gateway_routes.py` has raw credentials in `token`, `api_key`, `explicit_key` at every auth check.

### Layer 2: before_send (our hook)

Handles what key-name matching can't: LLM content (prompt text, response content) that lives in variables with generic names like `body`, `response`, `emitted`.

What it does:

| Data | Action | Rationale |
|------|--------|-----------|
| Request body keys (`model`, `stream`, `max_tokens`, etc.) | **Kept as-is** | Safe metadata, critical for debugging |
| Request body values (messages, system prompt) | **Replaced with type+length** (`<list len=5>`) | Contains user prompts |
| Headers (`content-type`, `accept`, `user-agent`, `x-request-id`) | **Kept as-is** | Safe, useful for debugging |
| All other headers (`authorization`, `x-api-key`, etc.) | **`[REDACTED]`** | May contain credentials |
| Cookies | **Removed entirely** | Session tokens |
| `server_name` | **Removed** | Host identity |
| Stack frame vars: `call_id`, `chunk_count`, `is_streaming`, `model` | **Kept as-is** | Safe debugging context |
| Stack frame vars: `body`, `messages`, `final_response`, etc. | **Replaced with type+length** | LLM content |
| Stack frame vars: `api_key`, `token`, etc. | **`[Filtered]`** (by EventScrubber) | Credentials |
| `KeyboardInterrupt`, `SystemExit` | **Dropped entirely** | Not real errors |

The `_LLM_CONTENT_VARS` set in `main.py` lists every variable name that carries LLM content at crash sites. This was determined by auditing every exception raise and crash-prone call in `pipeline/`, `gateway_routes.py`, and `streaming/`.

### What a Sentry error looks like after scrubbing

```
# Request
url: /v1/messages
headers: {content-type: application/json, authorization: [REDACTED], x-request-id: abc-123}
data: {model: claude-sonnet-4-20250514, max_tokens: 1024, stream: true, messages: <list len=5>}

# Stack trace vars
call_id = "uuid-123"              # kept — log correlation
chunk_count = 42                  # kept — debugging context
is_streaming = true               # kept — debugging context
body = <dict keys=['model', 'messages']>   # shape only, no content
api_key = [Filtered]              # scrubbed by EventScrubber
final_response = <dict keys=['id', 'content']>  # shape only
```

## Environment Configuration

### Settings (`settings.py`)

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `sentry_enabled` | `SENTRY_ENABLED` | `True` | Master toggle — set `false` to opt out |
| `sentry_dsn` | `SENTRY_DSN` | (hardcoded project DSN) | Override to send to a different Sentry project |
| `sentry_traces_sample_rate` | `SENTRY_TRACES_SAMPLE_RATE` | `0.0` | Performance tracing (disabled — we use OTel) |
| `sentry_server_name` | `SENTRY_SERVER_NAME` | `""` | Instance identifier in Sentry dashboard |

These settings are reused from the general config for Sentry tags:

| Setting | Env Var | Default | Sentry field |
|---------|---------|---------|-------------|
| `environment` | `ENVIRONMENT` | `development` | `environment` tag |
| `service_name` | `SERVICE_NAME` | `luthien-proxy` | Part of `release` tag |
| `service_version` | `SERVICE_VERSION` | `2.0.0` | Part of `release` tag |

### Per-Environment Setup

**Local development** (`.env`):
```bash
# Sentry active by default, using hardcoded DSN
SENTRY_SERVER_NAME=local-dev
# ENVIRONMENT defaults to "development"
```

**Tests** (`conftest.py`):
```bash
ENVIRONMENT=test
# Sentry stays active — test errors go to Sentry tagged environment=test
# Filter in Sentry dashboard: environment:test to see/hide them
```

**Docker self-hosted** (`.env` → `env_file: .env` in docker-compose):
```bash
SENTRY_SERVER_NAME=docker-prod  # or any identifier
ENVIRONMENT=production
```

**Railway SaaS** (`provisioner.py` sets these automatically):
```bash
ENVIRONMENT=railway
SENTRY_SERVER_NAME=railway-{instance-name}
```

**Opting out** (any environment):
```bash
SENTRY_ENABLED=false
```

## Sentry Dashboard Usage

### Filtering by Environment

All error events are tagged with `environment`. Use Sentry's search bar:

- `environment:development` — your local dev errors
- `environment:test` — errors from test suite runs (expected errors from test scenarios)
- `environment:railway` — errors from SaaS Railway instances
- `environment:production` — errors from self-hosted production deployments
- `!environment:test` — everything except test noise

### Filtering by Release

Events are tagged `release:luthien-proxy@{version}`. Use this to:

- Track which version introduced a regression
- See if a bug is fixed in a newer release
- Filter: `release:luthien-proxy@2.0.0`

### Filtering by Instance

`SENTRY_SERVER_NAME` shows up as `server_name` tag (when set, stripped from event body but kept as a Sentry tag via `sentry_sdk.init(server_name=...)`):

- `server_name:railway-acme-corp` — errors from a specific Railway tenant
- `server_name:local-dev` — your local machine

### Correlating with Logs

Every error event has `call_id` preserved in stack frame variables. Use it to find the corresponding request in:
- Gateway logs (`docker compose logs gateway | grep {call_id}`)
- Tempo traces (if OTel is enabled)
- Conversation history UI (`/history/`)

## Architecture Notes

### Initialization Timing

Sentry initializes at **module level** in `main.py` (lines 56-152), before `create_app()` runs. This matches the OTel initialization pattern (lines 50-54). Module-level is necessary because:
- Errors during app startup (lifespan, dependency initialization) must be captured
- The FastAPI integration hooks into the app automatically once `sentry_sdk.init()` is called
- `get_settings()` is available at module level (no async, no DB needed)

### What Sentry Does NOT Replace

| Concern | Tool | Why not Sentry |
|---------|------|---------------|
| Distributed tracing | OpenTelemetry + Tempo | Sentry traces are disabled (`traces_sample_rate=0.0`), we use OTel |
| Request/response logging | PostgreSQL `conversation_events` | Sentry sees errors only, not successful requests |
| Real-time monitoring | Redis pub/sub + Activity UI | Sentry is async, not real-time |
| Metrics | (planned: Prometheus) | Sentry is for errors, not counters |

### Relationship with `send_default_pii`

Set to `False`. This means Sentry will NOT collect:
- User IP addresses
- Cookies (we also strip them in `before_send` as defense in depth)
- User identifiers (email, username)
- Full request headers (sensitive ones filtered by EventScrubber + our allowlist)

## Extending the Scrubbing

### Adding a new sensitive variable name

If you add code with a new local variable that holds LLM content (e.g., `transformed_messages`), add it to `_LLM_CONTENT_VARS` in `main.py`:

```python
_LLM_CONTENT_VARS = {
    "body", "messages", ...,
    "transformed_messages",  # new
}
```

### Adding a new credential variable name

If you add code with a new local variable that holds API keys or tokens, add it to `_EXTRA_DENYLIST` in `main.py`:

```python
_EXTRA_DENYLIST = [
    "anthropic_api_key", ...,
    "new_service_api_key",  # new
]
```

### Server-side safety net

Configure Advanced Data Scrubbing in Sentry project settings as a backup:
- Rule: `[Mask] [Regex: sk-[a-zA-Z0-9-]+] from [$string]` — catches API keys in any string value
- Rule: `[Remove] [Anything] from [$http.headers.authorization]` — redundant but safe

## Troubleshooting

### "I see test errors flooding Sentry"

Filter them out: `!environment:test`. Tests set `ENVIRONMENT=test` via `conftest.py`. Expected test errors (invalid configs, mock DB failures) are tagged accordingly. Use the test environment view to spot unexpected test failures.

### "I want to disable Sentry entirely for local dev"

```bash
SENTRY_ENABLED=false
```

### "Sentry events have `[Filtered]` everywhere, I can't debug"

The EventScrubber is doing its job. The variable **name** is preserved — check what it was. For LLM content vars, the type and size are shown (e.g., `<list len=5>`). Cross-reference with `call_id` in your logs for full context.

### "I want to use my own Sentry project"

Override the DSN:
```bash
SENTRY_DSN=https://your-key@your-org.ingest.sentry.io/your-project
```

### "Railway instance errors aren't showing up"

Check that the Railway instance has `SENTRY_ENABLED` not set to `false`. The provisioner sets `ENVIRONMENT=railway` and `SENTRY_SERVER_NAME=railway-{name}` but relies on the hardcoded DSN default.
