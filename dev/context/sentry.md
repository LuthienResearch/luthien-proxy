# Sentry Error Tracking

How and why Sentry is integrated, what data it collects, and how to use it across environments.

**Added**: 2026-03-14
**Files**: `observability/sentry.py`, `settings.py:73-77`, `tests/conftest.py:35`, `saas_infra/provisioner.py:97-98`

---

## Why Sentry

The proxy had zero error tracking (flagged as HIGH in the March 2026 audit). Errors in production were invisible unless someone checked logs. Sentry gives us:

- Automatic error capture with stack traces across all deployments
- Error grouping, deduplication, and alerting
- Environment/release tagging to distinguish dev, test, Railway, and self-hosted instances
- Error collection from alpha/beta users (opt-in) to catch issues we can't reproduce locally

## Consent Model: Opt-In

**Disabled by default** — set `SENTRY_ENABLED=true` to enable. The `luthien onboard` CLI prompts for this during setup.

The DSN defaults to empty in `settings.py` and must be set via `SENTRY_DSN` env var (or provided during `luthien onboard`). A Sentry DSN is a write-only ingest key, not a secret. Anyone who extracts it can only submit error reports, not read data.

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

The `_LLM_CONTENT_VARS` set in `observability/sentry.py` lists every variable name that carries LLM content at crash sites. This was determined by auditing every exception raise and crash-prone call in `pipeline/`, `gateway_routes.py`, and `streaming/`.

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
| `sentry_enabled` | `SENTRY_ENABLED` | `False` | Master toggle — set `true` to opt in |
| `sentry_dsn` | `SENTRY_DSN` | `""` (empty) | Must be set to enable Sentry — `luthien onboard` provides a default |
| `sentry_traces_sample_rate` | `SENTRY_TRACES_SAMPLE_RATE` | `0.0` | Performance tracing (disabled — we use OTel) |
| `sentry_server_name` | `SENTRY_SERVER_NAME` | `""` | Instance identifier in Sentry dashboard |

These settings are reused from the general config for Sentry tags:

| Setting | Env Var | Default | Sentry field |
|---------|---------|---------|-------------|
| `environment` | `ENVIRONMENT` (or `RAILWAY_SERVICE_NAME` on Railway) | `development` | `environment` tag |
| `service_name` | `SERVICE_NAME` | `luthien-proxy` | Part of `release` tag |
| `service_version` | `SERVICE_VERSION` | package version via `importlib.metadata` (see `version.py`) | Part of `release` tag |

### Per-Environment Setup

**Local development** (`.env`):
```bash
SENTRY_ENABLED=true
SENTRY_DSN=https://178c87f543acaf02b3f154ee329679fa@o4511061292089344.ingest.us.sentry.io/4511061302575104
SENTRY_SERVER_NAME=local-dev
# ENVIRONMENT defaults to "development"
```

**Tests** (`conftest.py`):
```bash
ENVIRONMENT=test
SENTRY_ENABLED=false  # disabled to avoid burning Sentry quota on expected test errors
# Tests that verify scrubbing logic import the functions directly with a fake DSN
```

**Docker self-hosted** (`.env` → `env_file: .env` in docker-compose):
```bash
SENTRY_SERVER_NAME=docker-prod  # or any identifier
ENVIRONMENT=production
```

**Railway SaaS** (`provisioner.py` sets these automatically):
```bash
SENTRY_ENABLED=true
# TODO(https://trello.com/c/N7rqkasZ): replace with LuthienResearch org DSN once set up
SENTRY_DSN=https://178c87f543acaf02b3f154ee329679fa@o4511061292089344.ingest.us.sentry.io/4511061302575104
SENTRY_SERVER_NAME=railway-{instance-name}
# ENVIRONMENT is derived automatically from RAILWAY_SERVICE_NAME (injected by Railway)
# e.g. "luthien-proxy-demo", "luthien-test-e2e" — each deployment gets its own Sentry environment
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
- `environment:luthien-proxy-demo` — errors from the demo Railway deployment specifically
- `environment:luthien-test-e2e` — errors from the e2e Railway deployment specifically
- `environment:production` — errors from self-hosted production deployments
- `!environment:test` — everything except test noise

Railway deployments automatically get their own environment tag from `RAILWAY_SERVICE_NAME` (injected by Railway at runtime). No manual `ENVIRONMENT` config needed per deployment.

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

Sentry initializes at **module level** in `main.py` via `init_sentry()` (from `observability/sentry.py`), before `create_app()` runs. This matches the OTel initialization pattern. Module-level is necessary because:
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

If you add code with a new local variable that holds LLM content (e.g., `transformed_messages`), add it to `_LLM_CONTENT_VARS` in `observability/sentry.py`:

```python
_LLM_CONTENT_VARS = {
    "body", "messages", ...,
    "transformed_messages",  # new
}
```

### Adding a new credential variable name

If you add code with a new local variable that holds API keys or tokens, add it to `_EXTRA_DENYLIST` in `observability/sentry.py`:

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

Sentry is disabled in tests (`SENTRY_ENABLED=false` in `conftest.py`). If test errors still appear, check that `conftest.py` is being loaded (it's in `tests/`, the root test directory). The scrubbing functions are tested independently using direct imports and fake DSN.

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

Check that the Railway instance has `SENTRY_ENABLED=true` and `SENTRY_DSN` set. The provisioner sets both automatically along with `SENTRY_SERVER_NAME=railway-{name}`. The environment tag is derived automatically from `RAILWAY_SERVICE_NAME` (injected by Railway).
