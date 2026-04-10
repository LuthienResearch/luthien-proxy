# Architecture

This document describes how luthien-proxy is structured and how requests flow through it. It should take about 10 minutes to read. A [visual version](docs/architecture-visual.html) is also available (open in a browser).

## What Is Luthien

Luthien is an LLM gateway that sits between AI clients and the Anthropic API. It intercepts requests and responses, applying configurable **policies** that can observe, modify, or block LLM interactions. Think of it as a programmable HTTP proxy specifically designed for AI control.

```
Client (Claude Code, etc.)
    |
    v
Luthien Gateway (FastAPI)
    |-- Policy hook: transform request
    |-- Forward to Anthropic backend
    |-- Policy hook: transform response / stream events
    |
    v
Client receives (possibly modified) response
```

The gateway speaks the Anthropic Messages API natively:

- **`POST /v1/messages`** — the native Anthropic path, handled by the policy pipeline. Preserves Anthropic-specific features (extended thinking, tool use, prompt caching).
- **`/v1/{anything else}`** — transparent passthrough to `https://api.anthropic.com` so Claude Code endpoints like `/v1/messages/count_tokens` and `/v1/models` work without 404s. Passthrough requests authenticate the same way as `/v1/messages` but bypass the policy pipeline.

There is no OpenAI-format path. The gateway was simplified to Anthropic-only.

## Request Lifecycle

A `POST /v1/messages` request flows through four phases. The entry point is `process_anthropic_request()` in `src/luthien_proxy/pipeline/anthropic_processor.py`.

### 1. Ingest & Authenticate

`gateway_routes.py` receives the HTTP request and resolves three things in a linear dependency chain:

1. `get_request_credential` — extracts a `Credential` from the `Authorization: Bearer` or `x-api-key` header.
2. `verify_token` — validates the credential against the configured `AuthMode` (`proxy_key`, `passthrough`, or `both`). In passthrough/both modes, `CredentialManager.validate_credential()` checks Anthropic's free `count_tokens` endpoint, cached via Redis or an in-process cache.
3. `resolve_anthropic_client` — builds an `AnthropicClient` from the credential. The `x-anthropic-api-key` header, if present, is used to forward a different key to the backend than the one that authenticated the client.

### 2. Process Request

`_process_request()` parses the body, captures a `RawHttpRequest` snapshot of the original headers/body, extracts a `session_id` (from `metadata.user_id` or the `x-session-id` header), validates required fields (`model`, `messages`, `max_tokens`), and records a `pipeline.client_request` event.

A `PolicyContext` is then constructed carrying the `transaction_id` (call id), emitter, raw HTTP request, session id, user credential, and credential manager.

### 3. Execute Policy Around Backend I/O

The pipeline constructs a request-scoped `_AnthropicPolicyIO` (implementing `AnthropicPolicyIOProtocol`) that holds the mutable request payload and provides `complete()` / `stream()` methods against the `AnthropicClient`.

`_run_policy_hooks()` drives the policy by calling:

1. `on_anthropic_request(request, ctx)` — policy may return a modified request.
2. `io.complete(request)` (non-streaming) or `io.stream(request)` (streaming).
3. For each backend stream event: `on_anthropic_stream_event(event, ctx)` — policy may drop, transform, or emit additional events.
4. When the backend stream ends: `on_anthropic_stream_complete(ctx)` — policy may emit final events.
5. Non-streaming responses pass through `on_anthropic_response(response, ctx)`.

The policy never sees the IO layer or HTTP response directly; it only sees hook callbacks.

### 4. Send to Client

`_handle_execution_streaming()` and `_handle_execution_non_streaming()` take the emissions from the policy hooks, record observability events (`pipeline.client_response`, `transaction.backend_response`, etc.), reconstruct post-policy responses for history, and return either a FastAPI `StreamingResponse` emitting Anthropic SSE events or a `JSONResponse`.

Errors mid-stream (after headers are sent) are emitted as inline `error` SSE events via `_StreamErrorEvent`. Errors before the stream starts surface as `BackendAPIError` and are formatted in Anthropic's error shape by the global exception handler in `main.py`.

## Module Map

### Entry Points

| Module | Responsibility |
|--------|---------------|
| `main.py` | App factory (`create_app`), lifespan, dependency wiring, exception handlers, `__main__` entry point with argparse + uvicorn |
| `gateway_routes.py` | `/v1/messages` route, `/v1/*` passthrough proxy, credential extraction and validation |
| `dependencies.py` | `Dependencies` dataclass + FastAPI `Depends()` getters for injected services |
| `settings.py` | Auto-generated pydantic-settings `Settings` class (regenerated from `config_fields.py`) |
| `config_fields.py` | Single source of truth for all config fields (env var names, defaults, types, metadata) |
| `config_registry.py` | `ConfigRegistry` — resolves values through CLI > env > DB (`gateway_config`) > defaults, with provenance |
| `config.py` | YAML policy loader (`load_policy_from_yaml`, `_import_policy_class`, `_instantiate_policy`) |
| `auth.py` | Admin auth utilities (`verify_admin_token`, localhost bypass, session + bearer + x-api-key) |
| `session.py` | `/auth/login`, `/auth/logout`, `/login` page — form login that validates against `ADMIN_API_KEY` and sets a session cookie |
| `policy_manager.py` | `PolicyManager` — loads policy from DB or YAML at startup, hot-swaps at runtime with Redis distributed lock |
| `credential_manager.py` | `CredentialManager` — auth mode resolution, Anthropic credential validation, TTL'd cache, `on_backend_401` invalidation |
| `policy_composition.py` | `compose_policy()` — inserts a policy into an existing chain (supports `MultiSerialPolicy`) |

### Pipeline

| Module | Responsibility |
|--------|---------------|
| `pipeline/__init__.py` | Re-exports `process_anthropic_request` and `ClientFormat` |
| `pipeline/anthropic_processor.py` | `process_anthropic_request` — the full request lifecycle, `_AnthropicPolicyIO`, `_run_policy_hooks`, streaming + non-streaming handlers, span hierarchy |
| `pipeline/client_format.py` | `ClientFormat` enum — currently Anthropic-only |
| `pipeline/session.py` | `extract_session_id_from_anthropic_body`, `extract_session_id_from_headers` |
| `pipeline/policy_context_injection.py` | `inject_policy_awareness_anthropic` — optional system prompt injection listing active policies |
| `pipeline/stream_protocol_validator.py` | `validate_anthropic_event_ordering` — catches policies that emit malformed Anthropic SSE sequences |

There is no longer a `pipeline/processor.py`, no `streaming/` directory, and no separate `orchestration/` package — `anthropic_processor.py` contains both the executor and the client formatter logic.

### Policy Core

| Module | Responsibility |
|--------|---------------|
| `policy_core/base_policy.py` | `BasePolicy` — stateless singleton base with `freeze_configured_state()` guard, `get_config()` helper, `short_policy_name` |
| `policy_core/anthropic_execution_interface.py` | `AnthropicExecutionInterface` — runtime-checkable Protocol defining the four hooks; `AnthropicPolicyIOProtocol` — request-scoped I/O surface used by the executor |
| `policy_core/anthropic_hook_policy.py` | `AnthropicHookPolicy` — mixin supplying passthrough defaults for all four hooks |
| `policy_core/policy_context.py` | `PolicyContext` — per-request mutable state; carries transaction id, emitter, session id, credentials, typed request-state slots, OTel span helpers, `__deepcopy__` for parallel sub-policies |
| `policy_core/text_modifier_policy.py` | `TextModifierPolicy` — base class for text-only content transformations across streaming and non-streaming (handles text block / tool_use invariants automatically) |
| `policies/` | Concrete policy implementations (see below) |
| `policies/simple_policy.py` | `SimplePolicy` — buffers streaming blocks and exposes `simple_on_request` / `simple_on_response_content` / `simple_on_anthropic_tool_call` overrides. Anthropic-only. |

The older `OpenAIPolicyInterface`, `StreamingPolicyContext`, and `PolicyProtocol` abstractions have all been removed.

### Concrete Policies

All live in `src/luthien_proxy/policies/`:

| Policy | What it does |
|--------|--------------|
| `noop_policy.NoOpPolicy` | Pass-through. Default. |
| `all_caps_policy.AllCapsPolicy` | Uppercases all text content (uses `TextModifierPolicy`). |
| `string_replacement_policy.StringReplacementPolicy` | Regex-based text substitution. |
| `simple_llm_policy.SimpleLLMPolicy` | Calls a judge LLM to rewrite or approve responses. |
| `tool_call_judge_policy.ToolCallJudgePolicy` | Uses a judge LLM to allow/block tool calls; persists decisions to `conversation_judge_decisions`. |
| `debug_logging_policy.DebugLoggingPolicy` | Writes request/response payloads to `debug_logs` for inspection. |
| `conversation_link_policy.ConversationLinkPolicy` | Injects a link to the live conversation view into the response. |
| `dogfood_safety_policy.DogfoodSafetyPolicy` | Safety rails used when the gateway is dogfooding itself. |
| `multi_serial_policy.MultiSerialPolicy` | Runs multiple policies sequentially; drives composition via `policy_composition.compose_policy`. |
| `onboarding_policy.OnboardingPolicy` / `hackathon_onboarding_policy.HackathonOnboardingPolicy` | Welcome-and-guide policies used during first-run flows. |
| `hackathon_policy_template.HackathonPolicyTemplate` | Template scaffolding for hackathon participants. |
| `sample_pydantic_policy.SamplePydanticPolicy` | Example showing Pydantic-model-based config. |
| `simple_noop_policy.SimpleNoOpPolicy` | `SimplePolicy` demo passthrough. |
| `presets/` | Ready-made presets (`block_dangerous_commands`, `block_sensitive_file_writes`, `block_web_requests`, `no_apologies`, `no_yapping`, `plain_dashes`, `prefer_uv`). |

Shared helpers: `multi_policy_utils.py`, `simple_llm_utils.py`, `tool_call_judge_utils.py`.

### LLM Clients

| Module | Responsibility |
|--------|---------------|
| `llm/anthropic_client.py` | `AnthropicClient` — async wrapper around the Anthropic SDK for `complete()` and `stream()`; supports API key and OAuth bearer auth. |
| `llm/anthropic_client_cache.py` | LRU cache of `AnthropicClient` instances keyed by credential, so passthrough requests reuse connection pools. |
| `llm/judge_client.py` | `judge_completion` — thin wrapper around LiteLLM `acompletion` used by judge/LLM policies (LiteLLM is retained only as a multi-provider client for judge calls, not for gateway traffic). |
| `llm/types/anthropic.py` | `AnthropicRequest`, `AnthropicResponse`, `AnthropicContentBlock`, `build_usage` — TypedDicts used throughout the pipeline. |

There is no `llm/litellm_client.py` — the gateway no longer proxies via LiteLLM.

### Credentials

| Module | Responsibility |
|--------|---------------|
| `credentials/credential.py` | `Credential` (frozen dataclass), `CredentialType` (`API_KEY`, `AUTH_TOKEN`), `CredentialError`. |
| `credentials/auth_provider.py` | `AuthProvider` base + `ServerKey`, `UserCredentials`, `UserThenServer` resolvers used by policies that need an outbound credential. |
| `credentials/store.py` | `CredentialStore` — DB-backed server credential storage (`server_credentials` table) with optional Fernet encryption. |

### Storage & Observability

| Module | Responsibility |
|--------|---------------|
| `storage/events.py` | `reconstruct_full_response_from_chunks` — reassembles streamed chunks into a full response dict for history. |
| `observability/emitter.py` | `EventEmitter` — fire-and-forget multi-sink recorder (stdout + `conversation_calls`/`conversation_events` DB rows + `EventPublisher` + current OTel span as span events). Defines `EventEmitterProtocol` + `NullEventEmitter`. |
| `observability/event_publisher.py` | `EventPublisherProtocol`, `InProcessEventPublisher` — the SSE activity stream transport. |
| `observability/redis_event_publisher.py` | `RedisEventPublisher` — Redis pub/sub implementation of the SSE activity stream. |
| `observability/sentry.py` | `init_sentry` — optional Sentry integration. |
| `telemetry.py` | OpenTelemetry setup: `configure_tracing`, `configure_logging`, `instrument_app`, `instrument_redis`, `restore_context`. |

**Important distinction:** `EventEmitter` is the high-level multi-sink dispatcher. `EventPublisherProtocol` is *one* of its sinks — specifically the SSE activity-feed transport. "Emitter" fans out to many sinks; "Publisher" is a single sink.

### Admin, UI, History, Logs, Debug

| Module | Responsibility |
|--------|---------------|
| `admin/routes.py` | `/api/admin/*` — policy current/set/list, `/models`, `/test/chat`, auth config, cached credentials management, server credentials CRUD, telemetry config, gateway settings (deprecated), unified config get/put/delete |
| `admin/policy_discovery.py` | Discovers installed policy classes for the admin UI dropdown. |
| `ui/routes.py` | HTML pages and the SSE activity stream. Routes: `/` (landing), `/activity/monitor` (301 → `/history`), `/debug/activity` (raw SSE viewer), `/diffs`, `/policy-config`, `/config` (config dashboard), `/credentials`, `/request-logs/viewer`, `/conversation/live/{id}`, `/client-setup`, and the `GET /api/activity/stream` SSE endpoint. Also redirects deprecated `/debug/diff` and `/admin/*` paths. |
| `history/routes.py` | `/history` (list sessions), `/history/session/{id}` (session detail); `/api/history/*` JSON API. |
| `history/service.py` | Session list + detail query builders, turn reconstruction, markdown/JSONL export. |
| `history/models.py` | Pydantic models for session list/detail responses. |
| `request_log/routes.py` | `/request-logs` (list), `/request-logs/{transaction_id}` (detail). The UI page `/request-logs/viewer` is served from `ui/routes.py`. |
| `request_log/recorder.py` | `RequestLogRecorder` + `create_recorder` — wired into the pipeline to capture inbound and outbound HTTP envelopes. |
| `request_log/sanitize.py` | Header/body sanitization for stored logs. |
| `request_log/service.py`, `request_log/models.py` | Query helpers and response models. |
| `debug/routes.py` | `/api/debug/calls`, `/api/debug/calls/{call_id}`, `/api/debug/calls/{call_id}/diff` — raw conversation event inspection and diffs. |

### Usage Telemetry

| Module | Responsibility |
|--------|---------------|
| `usage_telemetry/collector.py` | `UsageCollector` — in-memory atomic counters (`requests_accepted`, `requests_completed`, `input_tokens`, `output_tokens`, streaming/non-streaming splits, distinct sessions). |
| `usage_telemetry/sender.py` | `TelemetrySender` — periodic background task that POSTs snapshots to the telemetry endpoint. |
| `usage_telemetry/config.py` | `resolve_telemetry_config` — per-deployment id + enabled state loaded from `telemetry_config` table. |

### Utilities

| Module | Responsibility |
|--------|---------------|
| `utils/db.py` | `DatabasePool` — asyncpg pool with SQLite fallback. |
| `utils/db_sqlite.py` | SQLite-specific variant used when `DATABASE_URL=sqlite://...`. |
| `utils/sqlite_migrations/` | Auto-applied SQLite migrations mirroring `migrations/sqlite/`. |
| `utils/migration_check.py` | `check_migrations` — asserts DB schema is current at startup. |
| `utils/redis_client.py` | Shared Redis helpers. |
| `utils/credential_cache.py` | `CredentialCacheProtocol`, `InProcessCredentialCache`, `RedisCredentialCache`. |
| `utils/constants.py` | Shared constants (request size limits, Redis lock TTL, etc.). |
| `utils/url.py` | `sanitize_url_for_logging`. |

### Static Assets

`static/` ships the HTML/JS/CSS for the landing page, activity monitor, diff viewer, config dashboard, credentials UI, history UI, request-log viewer, and conversation live view. `main.py` mounts it at `/static` with cache-control rules that force revalidation for JS/HTML/CSS.

## External Dependencies

| Service | Used For | Required? |
|---------|----------|-----------|
| **PostgreSQL or SQLite** | Conversation events, policy events, policy config, auth config, request logs, telemetry config, server credentials, gateway config, debug logs. Schema lives in `migrations/postgres/` and `migrations/sqlite/`. | Yes (SQLite is the default for dockerless dev) |
| **Redis** | Credential validation cache, activity event pub/sub (SSE), policy config distributed lock, last-credential-type metadata. | No — in-process replacements are used when `REDIS_URL` is empty |

When `REDIS_URL` is unset, the gateway runs in **local mode**: single-process with `InProcessEventPublisher`, `InProcessCredentialCache`, and no distributed policy lock. This is appropriate for single-user local development. Docker Compose deployments set `REDIS_URL` to enable multi-worker operation.

### Observability Pipeline

Events flow through a multi-sink emitter:

```
Application code
    |
    v
EventEmitter (observability/emitter.py)
    |-- stdout (structured logging)
    |-- Database (conversation_calls + conversation_events)
    |-- Current OTel span (as a span event)
    |-- EventPublisher (activity SSE stream)
           |-- RedisEventPublisher (when Redis available)
           |-- InProcessEventPublisher (local mode)
                    |
                    v
              /api/activity/stream SSE endpoint (ui/routes.py)
```

OpenTelemetry spans are created around each pipeline phase (`anthropic_transaction_processing` → `process_request` / `process_response` → `policy_execute` / `send_upstream` / `send_to_client`). `PolicyContext.span(...)` lets policies open child spans for their own work.

### Configuration Surfaces

Settings live in different places depending on their nature:

| Surface | What goes here | Example |
|---------|---------------|---------|
| `Settings` (pydantic-settings, env vars) | Infrastructure and static config | `DATABASE_URL`, `REDIS_URL`, `GATEWAY_PORT`, `SENTRY_DSN`, `ANTHROPIC_API_KEY` |
| `ConfigRegistry` (`gateway_config` table) | Runtime-tunable config fields declared in `config_fields.py` (CLI > env > DB > default) | `auth_mode`, `inject_policy_context`, `log_level` |
| YAML via `POLICY_CONFIG` | Policy class selection and per-policy config | Policy class ref, judge model, threshold |
| Admin API (`/api/admin/*`) | Runtime-changeable settings and the active policy | Active policy, auth config, server credentials, telemetry config, gateway_config fields |

`config_fields.py` is the single source of truth for all config fields. `settings.py` and `.env.example` are auto-generated from it (`scripts/generate_settings.py`, `scripts/generate_env_example.py`). Don't hand-edit either.

## Development from Worktrees

Docker Compose does **not** work reliably from worktrees — volume mounts resolve relative to the main repo, not the worktree. Instead, run the gateway directly:

```bash
./scripts/start_gateway.sh
```

This starts the gateway in dockerless local mode (SQLite at `~/.luthien/local.db`, no Redis, in-process pub/sub). The activity monitor, admin API, history UI, and request-log viewer all work. You can also invoke `python -m luthien_proxy.main --local` directly.

For e2e tests from a worktree, use `sqlite_e2e` or `mock_e2e` markers which start an in-process gateway automatically:

```bash
uv run pytest -m sqlite_e2e tests/luthien_proxy/e2e_tests/ --no-cov
./scripts/run_e2e.sh  # runs all tiers, stops on first failure
```

## Key Abstractions

### BasePolicy

All policies inherit from `BasePolicy`. It provides:

- `short_policy_name` for identification (defaults to the class name).
- `active_policy_names()` returning this policy's leaf names (multi-policies recurse; `NoOpPolicy` returns `[]`).
- `get_config()` auto-extracts configuration from Pydantic-model instance attributes.
- `freeze_configured_state()` — load-time check that rejects mutable container attributes on the instance (policies are singletons shared across concurrent requests).
- `_resolve_judge_api_key()` — priority chain (explicit → passthrough → server fallback) for policies that make their own backend calls.

### AnthropicExecutionInterface

The one-and-only policy contract. A runtime-checkable `Protocol` with four hooks:

```python
async def on_anthropic_request(self, request, context) -> AnthropicRequest
async def on_anthropic_response(self, response, context) -> AnthropicResponse
async def on_anthropic_stream_event(self, event, context) -> list[MessageStreamEvent]
async def on_anthropic_stream_complete(self, context) -> list[AnthropicPolicyEmission]
```

The executor calls `on_anthropic_request` before the backend call, then either `on_anthropic_response` (non-streaming) or `on_anthropic_stream_event` for each event plus `on_anthropic_stream_complete` (streaming). Policies never see the IO layer directly — the executor owns `AnthropicPolicyIOProtocol` and drives all backend calls.

`AnthropicHookPolicy` is a mixin supplying passthrough defaults, so you only override the hooks you care about.

### PolicyContext

Created per request. Carries:

- `transaction_id` — unique ID for this request/response cycle (used as `call_id` in the DB).
- `emitter` — fire-and-forget event recording into the multi-sink pipeline.
- `raw_http_request` — the original inbound `RawHttpRequest` snapshot (headers, body, method, path).
- `session_id` — optional client session tracking.
- `user_credential` + `credential_manager` — so policies that make backend calls can resolve the right outbound key.
- `get_request_state(owner, type, factory)` — typed per-policy state keyed by `(id(policy_instance), type)`, preventing collisions between policies.
- `scratchpad` — untyped dict for quick prototyping.
- `span(name)` / `add_span_event(...)` — OpenTelemetry helpers that automatically tag spans with the transaction id.
- `__deepcopy__` — creates independent copies for parallel sub-policy execution while sharing non-copyable infrastructure (emitter, credential manager).

### SimplePolicy

A convenience base class (in `policies/simple_policy.py`) that buffers Anthropic streaming content and surfaces three simple override points:

```python
async def simple_on_request(self, request_str: str, context) -> str
async def simple_on_response_content(self, content: str, context) -> str
async def simple_on_anthropic_tool_call(self, tool_call, context) -> tool_call
```

It trades streaming responsiveness for implementation simplicity. Anthropic-only — there is no OpenAI-format variant anymore.

### TextModifierPolicy

A base class in `policy_core/text_modifier_policy.py` for text-only transformations. Override `modify_text(text) -> text` and/or `extra_text() -> str | None`. The base class handles all the Anthropic SSE plumbing, including the invariant that text blocks must precede tool_use blocks. Used by `AllCapsPolicy` and similar.

## Data Model

All tables live in the `luthien_control` database (Postgres in Docker Compose, SQLite in dockerless mode). Migrations are in `migrations/postgres/` and `migrations/sqlite/` with matching numeric prefixes.

### Conversation Tracking

```
conversation_calls: one row per API call
  - call_id (PK, TEXT), model_name, provider, status, session_id, created_at, completed_at

conversation_events: request and response events per call
  - id (PK, UUID), call_id (FK → conversation_calls, CASCADE), event_type,
    payload (JSONB), session_id, created_at
  - ordered by created_at (no sequence column — dropped in migration 004)

policy_events: policy decisions and modifications per call
  - id (PK, UUID), call_id (FK → conversation_calls, CASCADE), policy_class,
    policy_config (JSONB), event_type,
    original_event_id (FK → conversation_events, SET NULL),
    modified_event_id (FK → conversation_events, SET NULL),
    metadata (JSONB), created_at

conversation_judge_decisions: LLM judge traces (ToolCallJudgePolicy)
  - id (PK, UUID), call_id (FK → conversation_calls, CASCADE), trace_id, tool_call_id,
    probability, explanation, tool_call (JSONB), judge_prompt (JSONB),
    judge_response_text, original_request (JSONB), original_response (JSONB),
    stream_chunks (JSONB), blocked_response (JSONB),
    timing (JSONB), judge_config (JSONB), created_at
```

Events store both original (pre-policy) and final (post-policy) data, enabling diff views in `/diffs` and `/api/debug/calls/{id}/diff`.

### HTTP Request Logs

```
request_logs: raw HTTP-level logging (client↔proxy and proxy↔backend)
  - id (PK, UUID), transaction_id, session_id, direction ("inbound" | "outbound"),
    http_method, url, request_headers (JSONB), request_body (JSONB),
    response_status, response_headers (JSONB), response_body (JSONB),
    started_at, completed_at, duration_ms, model, is_streaming, endpoint, error, created_at
```

Only populated when `enable_request_logging` is set. Exposed via `/request-logs` and `/request-logs/viewer`.

### Single-Row Config Tables

These tables enforce exactly one row via `CHECK (id = 1)`:

```
current_policy: active policy configuration
  - policy_class_ref, config (JSONB), enabled_at, enabled_by
  - Protected by Redis distributed lock on changes

auth_config: gateway authentication settings
  - auth_mode ("client_key" | "passthrough" | "both"), validate_credentials,
    valid_cache_ttl_seconds, invalid_cache_ttl_seconds, updated_at, updated_by

telemetry_config: usage telemetry opt-out + deployment identity
  - enabled (null = default on), deployment_id (UUID), updated_at, updated_by
```

### Key-Value Config

```
gateway_config: runtime-tunable fields declared in config_fields.py
  - key (PK, TEXT), value (TEXT), updated_at, updated_by
  - Resolved via ConfigRegistry in priority order: CLI > env > DB > default
```

### Server Credentials

```
server_credentials: operator-provisioned API keys used by policies that declare an auth_provider
  - name (PK, TEXT, UNIQUE), platform, platform_url, credential_type,
    credential_value, is_encrypted, expiry, owner, scope, created_at, updated_at
```

### Debug Logs

```
debug_logs: general-purpose debug storage
  - id (PK, UUID), time_created, debug_type_identifier, jsonblob (JSONB)
```

## How to Add a New Policy

1. Create `src/luthien_proxy/policies/my_policy.py`.
2. Choose a base class:
   - **`TextModifierPolicy`** — text-only transformations. Override `modify_text` and/or `extra_text`. Everything else is handled.
   - **`SimplePolicy`** — buffers streaming blocks and exposes `simple_on_request`, `simple_on_response_content`, `simple_on_anthropic_tool_call`. Content is only delivered on block completion — use this when you don't need streaming responsiveness.
   - **`BasePolicy` + `AnthropicHookPolicy`** — full control. Override any of the four lifecycle hooks. Use `PolicyContext.get_request_state(self, StateType, factory)` for per-request state. Use this when you need to buffer blocks yourself, make multiple backend calls, or implement complex multi-turn patterns (e.g., an overseer that inspects tool use and decides whether to proceed).
3. Add a Pydantic config model if your policy needs configuration — `BasePolicy.get_config()` will serialize it automatically.
4. Enable via YAML config or the admin API:

```yaml
# config/policy_config.yaml
policy:
  class: "luthien_proxy.policies.my_policy:MyPolicy"
  config:
    some_setting: "value"
```

Or at runtime:

```bash
curl -X POST http://localhost:8000/api/admin/policy/set \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -d '{"policy_class_ref": "luthien_proxy.policies.my_policy:MyPolicy", "config": {}}'
```

### Policy Rules

- Policies are **singletons** — never store request-scoped data on `self`. Use `context.get_request_state()`.
- Config-time collections must be immutable (`tuple`, `frozenset`). `freeze_configured_state()` enforces this at load time and raises `TypeError` on any mutable container attribute.
- For `SimplePolicy`, streaming content is buffered until block completion; your hook runs on the complete content.
- For full hook-based policies, every event returned from `on_anthropic_stream_event` is forwarded to the client, and the executor validates ordering against Anthropic's SSE protocol (`pipeline/stream_protocol_validator.py`).
