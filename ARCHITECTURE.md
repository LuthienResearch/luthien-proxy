# Architecture

This document describes how luthien-proxy is structured and how requests flow through it. It should take about 10 minutes to read. A [visual version](docs/architecture-visual.html) is also available (open in a browser).

## What Is Luthien

Luthien is an LLM gateway that sits between AI clients and backend LLM providers. It intercepts requests and responses, applying configurable **policies** that can observe, modify, or block LLM interactions. Think of it as a programmable HTTP proxy specifically designed for AI control.

```
Client (Claude Code, etc.)
    |
    v
Luthien Gateway (FastAPI)
    |-- Policy: inspect/modify request
    |-- Forward to backend LLM
    |-- Policy: inspect/modify response
    |
    v
Client receives (possibly modified) response
```

The gateway supports the Anthropic API format natively:
- **Anthropic** (`/v1/messages`) — native Anthropic SDK, preserving features like extended thinking and prompt caching

## Request Lifecycle

A request flows through four phases. The entry point is `process_anthropic_request()` in `src/luthien_proxy/pipeline/`.

### 1. Ingest & Authenticate

`gateway_routes.py` receives the HTTP request, verifies the API key (proxy key, passthrough, or both modes via `CredentialManager`), and dispatches to the appropriate pipeline processor.

### 2. Policy on Request

A `PolicyContext` is created for this request (unique `transaction_id`, session tracking, observability emitter). The policy's request hook runs, potentially modifying the request before it reaches the backend.

### 3. Send to Backend

The (possibly modified) request is forwarded to the backend LLM via `AnthropicClient`.

### 4. Policy on Response & Send to Client

**Non-streaming:** The complete response passes through the policy's response hook, then is returned as JSON.

**Streaming:** A two-stage async pipeline connected by `asyncio.Queue`s:

```
Backend stream (ModelResponse chunks)
    |
    v
PolicyExecutor: assembles chunks into blocks, fires policy hooks
    |  (on_content_complete, on_tool_call_complete, etc.)
    v
ClientFormatter: converts ModelResponse -> SSE strings
    |
    v
Client receives SSE events
```

**Streaming (Anthropic path):** The executor calls policy hooks around backend I/O. Policies transform requests/responses via lifecycle hooks (`on_anthropic_request`, `on_anthropic_stream_event`, etc.) and the executor formats events as SSE for the client.

## Module Map

### Core Pipeline

| Module | Responsibility |
|--------|---------------|
| `main.py` | App factory, lifespan management, dependency wiring |
| `gateway_routes.py` | HTTP endpoints, authentication |
| `pipeline/processor.py` | OpenAI request pipeline (phases 1-4) |
| `pipeline/anthropic_processor.py` | Anthropic request pipeline (phases 1-4) |
| `dependencies.py` | DI container (`Dependencies` dataclass), FastAPI `Depends()` functions |

### Policy System

| Module | Responsibility |
|--------|---------------|
| `policy_core/base_policy.py` | `BasePolicy` — shared base with config helpers and singleton safety checks |
| `policy_core/openai_interface.py` | `OpenAIPolicyInterface` — abstract hooks for OpenAI-format request/response |
| `policy_core/anthropic_execution_interface.py` | `AnthropicExecutionInterface` — execution-oriented Anthropic policy contract |
| `policy_core/anthropic_hook_policy.py` | `AnthropicHookPolicy` — hook-based base class with overridable lifecycle methods |
| `policy_core/policy_context.py` | `PolicyContext` — per-request mutable state (transaction ID, emitter, typed state slots) |
| `policy_core/streaming_policy_context.py` | `StreamingPolicyContext` — streaming-specific context (egress queue, stream state, keepalive) |
| `policy_core/policy_protocol.py` | `PolicyProtocol` — structural typing protocol for policy infrastructure |
| `policy_manager.py` | Runtime policy loading, hot-swapping, DB persistence |
| `policies/` | Concrete policy implementations |

### Streaming Infrastructure

| Module | Responsibility |
|--------|---------------|
| `orchestration/policy_orchestrator.py` | `PolicyOrchestrator` — wires PolicyExecutor + ClientFormatter + queues |
| `streaming/policy_executor/` | Chunk assembly, policy hook dispatch, timeout management |
| `streaming/client_formatter/` | ModelResponse -> SSE string conversion |
| `streaming/stream_blocks.py` | `ContentStreamBlock`, `ToolCallStreamBlock` — accumulated block types |
| `streaming/stream_state.py` | `StreamState` — tracks blocks, finish reason, raw chunks during streaming |

### Storage & Observability

| Module | Responsibility |
|--------|---------------|
| `storage/events.py` | Conversation event reconstruction utilities |
| `observability/emitter.py` | `EventEmitter` — fire-and-forget event recording (DB + Redis + stdout) |


### Configuration & Authentication

| Module | Responsibility |
|--------|---------------|
| `settings.py` | `Settings` (pydantic-settings) — centralized env var loading with validation and defaults |
| `credential_manager.py` | `CredentialManager` — auth mode resolution (proxy key, passthrough, or both), Anthropic credential validation with Redis caching |
| `config/` | YAML policy config loading |

### Other

| Module | Responsibility |
|--------|---------------|
| `llm/litellm_client.py` | OpenAI-format backend calls via LiteLLM |
| `llm/anthropic_client.py` | Anthropic-format backend calls via native SDK |
| `admin/` | Runtime policy management API (`/api/admin/*`) |
| `ui/` | Activity monitor, diff viewer (`/activity/*`, `/diffs`) |
| `history/` | Conversation history API and UI (`/history/*`) |
| `request_log/` | HTTP request logging, header sanitization, log viewer UI (`/logs/*`) |
| `debug/` | Debug endpoints for inspecting conversation events |
| `usage_telemetry/` | `UsageCollector` — in-memory aggregate metrics (request counts, token counts), periodic send to telemetry endpoint |

## External Dependencies

| Service | Used For | Required? |
|---------|----------|-----------|
| **PostgreSQL** | Conversation storage, policy config, auth config, request logs, telemetry config | Yes (or SQLite for local mode) |
| **Redis** | Credential validation cache, activity event pub/sub (SSE), policy config distributed lock, last-credential-type metadata | No — in-process replacements used when `REDIS_URL` is empty |

When `REDIS_URL` is unset, the gateway runs in **local mode**: single-process with in-process pub/sub (`InProcessEventPublisher`), in-process credential cache (`InProcessCredentialCache`), and no distributed policy lock. This is appropriate for single-user local development.

### Observability Pipeline

Events flow through a multi-sink emitter:

```
Application code
    |
    v
EventEmitter (observability/emitter.py)
    |-- stdout (structured logging)
    |-- Database (conversation_events)
    |-- EventPublisher (activity SSE stream)
           |-- RedisEventPublisher (when Redis available)
           |-- InProcessEventPublisher (local mode)
                    |
                    v
              /api/activity/stream SSE endpoint (ui/routes.py)
```

`EventEmitter` is the high-level multi-sink dispatcher. `EventPublisherProtocol` is the transport layer for the SSE activity stream specifically. Don't confuse the two — "Emitter" dispatches to multiple sinks, "Publisher" is one specific sink (the SSE activity feed).

### Configuration Surfaces

Settings live in different places depending on their nature:

| Surface | What goes here | Example |
|---------|---------------|---------|
| `Settings` (pydantic-settings, env vars) | Infrastructure config | `DATABASE_URL`, `REDIS_URL`, `GATEWAY_PORT`, `SENTRY_DSN` |
| YAML via `POLICY_CONFIG` | Policy selection and policy-specific config | Policy class, model, threshold |
| Admin API (`/api/admin/*`) | Runtime-changeable settings | Active policy, auth mode, gateway settings |
| CLI config (`~/.luthien/config.toml`) | Per-user CLI preferences | Repo path, default policy |

## Development from Worktrees

Docker Compose does **not** work reliably from worktrees — volume mounts resolve relative to the main repo, not the worktree. Instead, run a local dev server directly:

```bash
DATABASE_URL=sqlite:///tmp/luthien-dev.db REDIS_URL="" \
  uv run uvicorn luthien_proxy.main:create_app --factory --port 8001
```

This starts the gateway in local mode (SQLite + in-process pub/sub). The activity monitor, admin API, and history UI all work. Add `tmp/` to your global gitignore to avoid committing SQLite files.

For e2e tests from a worktree, use `sqlite_e2e` markers which start an in-process gateway automatically:

```bash
uv run pytest -m sqlite_e2e tests/luthien_proxy/e2e_tests/ --no-cov
```

## Key Abstractions

### BasePolicy

All policies inherit from `BasePolicy`. It provides:
- `short_policy_name` for identification
- `get_config()` for serializing policy configuration
- `freeze_configured_state()` — load-time check that rejects mutable containers on the instance (policies are singletons shared across concurrent requests)

### OpenAIPolicyInterface

Defines hooks for the OpenAI format pipeline. Two required hooks for non-streaming:
- `on_openai_request(request, context) -> request`
- `on_openai_response(response, context) -> response`

Plus streaming hooks that fire as chunks arrive: `on_chunk_received`, `on_content_delta`, `on_content_complete`, `on_tool_call_delta`, `on_tool_call_complete`, `on_finish_reason`, `on_stream_complete`, `on_streaming_policy_complete`.

### AnthropicExecutionInterface

A hook-based contract where policies implement lifecycle hooks and the executor drives backend I/O:

```python
async def on_anthropic_request(self, request, context) -> AnthropicRequest
async def on_anthropic_response(self, response, context) -> AnthropicResponse
async def on_anthropic_stream_event(self, event, context) -> list[MessageStreamEvent]
async def on_anthropic_stream_complete(self, context) -> list[AnthropicPolicyEmission]
```

The executor calls `on_anthropic_request` before the backend call, then either `on_anthropic_response` (non-streaming) or `on_anthropic_stream_event` + `on_anthropic_stream_complete` (streaming). Policies never see the IO layer directly.

### PolicyContext

Created per-request. Carries:
- `transaction_id` — unique ID for this request/response cycle
- `emitter` — fire-and-forget event recording
- `get_request_state(owner, type, factory)` — typed per-policy state keyed by `(policy_instance, type)`, preventing key collisions between policies
- `session_id` — optional client session tracking
- Span helpers for OpenTelemetry tracing

### SimplePolicy

A convenience base class that buffers streaming content and surfaces three simple override points:

```python
async def simple_on_request(self, request_str: str, context) -> str
async def simple_on_response_content(self, content: str, context) -> str
async def simple_on_response_tool_call(self, tool_call, context) -> tool_call
```

Supports both OpenAI and Anthropic formats. Trades streaming responsiveness for implementation simplicity.

## Data Model

All tables live in the `luthien_control` Postgres database. Migrations are in `migrations/`.

### Conversation Tracking

```
conversation_calls: one row per API call
  - call_id (PK), model_name, provider, status, session_id, created_at, completed_at

conversation_events: request and response events per call
  - id (PK, UUID), call_id (FK → conversation_calls, CASCADE), event_type, payload (JSONB), session_id, created_at

policy_events: policy decisions and modifications per call
  - id (PK, UUID), call_id (FK → conversation_calls, CASCADE), policy_class, policy_config (JSONB),
    event_type, original_event_id (FK → conversation_events), modified_event_id (FK → conversation_events),
    metadata (JSONB), created_at

conversation_judge_decisions: LLM judge traces (ToolCallJudgePolicy)
  - id (PK, UUID), call_id (FK → conversation_calls, CASCADE), trace_id, tool_call_id,
    probability, explanation, tool_call (JSONB), judge_prompt (JSONB), judge_response_text,
    original_request (JSONB), original_response (JSONB), blocked_response (JSONB),
    timing (JSONB), judge_config (JSONB), created_at
```

Events store both original (pre-policy) and final (post-policy) data, enabling diff views.

### HTTP Request Logs

```
request_logs: raw HTTP-level logging (client↔proxy and proxy↔backend)
  - id (PK, UUID), transaction_id, session_id, direction ("inbound" | "outbound"),
    http_method, url, request_headers (JSONB), request_body (JSONB),
    response_status, response_headers (JSONB), response_body (JSONB),
    started_at, completed_at, duration_ms, model, is_streaming, endpoint, error, created_at
```

### Single-Row Config Tables

These tables enforce exactly one row via `CHECK (id = 1)`:

```
current_policy: active policy configuration
  - policy_class_ref, config (JSONB), enabled_at, enabled_by
  - Protected by Redis distributed lock on changes

auth_config: gateway authentication settings
  - auth_mode ("proxy_key" | "passthrough" | "both"), validate_credentials,
    valid_cache_ttl_seconds, invalid_cache_ttl_seconds, updated_at, updated_by

telemetry_config: usage telemetry opt-out
  - enabled (null = default on), deployment_id (UUID), updated_at, updated_by
```

### Debug Logs

```
debug_logs: general-purpose debug storage
  - id (PK, UUID), time_created, debug_type_identifier, jsonblob (JSONB)
```

## How to Add a New Policy

1. Create `src/luthien_proxy/policies/my_policy.py`
2. Choose your base class:
   - **`SimplePolicy`** — easiest: override `simple_on_request`, `simple_on_response_content`, `simple_on_response_tool_call`. Works with both OpenAI and Anthropic formats. Content is buffered until block completion, then your hook runs on the complete content. Use this when you only need to inspect or transform finished text/tool calls and don't need to control streaming behavior.
   - **`BasePolicy` + `OpenAIPolicyInterface` + `AnthropicExecutionInterface`** — full control over streaming and both API formats. Use this when you need to buffer blocks yourself, make multiple backend calls, reconstruct streaming events, or implement complex multi-turn patterns (e.g., an overseer that inspects tool use and decides whether to proceed). Per-request state goes on `PolicyContext` via `context.get_request_state(self, StateType, factory)`.
3. Add a Pydantic config model if your policy needs configuration
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
- Config-time collections must be immutable (`tuple`, `frozenset`). `freeze_configured_state()` enforces this.
- For `SimplePolicy`, streaming content is buffered until block completion, then your hook runs on the complete content.
- For full `OpenAIPolicyInterface`, you control what gets pushed to the egress queue via `ctx.push_chunk()`.
