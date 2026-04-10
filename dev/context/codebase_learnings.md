# Codebase Learnings

Architectural patterns, module relationships, and how subsystems work together.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points or prose).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Architecture Overview (2025-10-24, updated 2026-04-10)

Integrated single-process gateway. Authoritative module map and request lifecycle live in `ARCHITECTURE.md`. Summary of directories referenced from these context notes:

- **Gateway** (`src/luthien_proxy/`): FastAPI app with built-in policy enforcement; Anthropic requests flow through `pipeline/anthropic_processor.py` and use `AnthropicClient` (wraps the Anthropic SDK) to talk to the backend.
- **Pipeline** (`src/luthien_proxy/pipeline/`): Request processors for the Anthropic path, including streaming execution.
- **Policies** (`src/luthien_proxy/policies/`): Concrete policy implementations (noop, simple, tool-call judge, etc.).
- **Policy Core** (`src/luthien_proxy/policy_core/`): Policy base classes, protocols, per-request `PolicyContext`, Anthropic execution interface.
- **Storage** (`src/luthien_proxy/storage/`): Conversation event persistence with a background queue.
- **Observability** (`src/luthien_proxy/observability/`): OpenTelemetry integration and structured transaction recording.
- **Admin** (`src/luthien_proxy/admin/`): Runtime policy management API.
- **Debug** (`src/luthien_proxy/debug/`): Endpoints for inspecting conversation events.
- **UI** (`src/luthien_proxy/ui/`): Activity monitoring and diff viewer interfaces.
- **LLM** (`src/luthien_proxy/llm/`): `AnthropicClient` (Anthropic SDK wrapper) plus a LiteLLM-backed `judge_client` used by judge-driven policies. The main request path does not go through LiteLLM.

## Anthropic Runtime Model (2026-02-27, updated 2026-04-10)

- Anthropic request handling is hook-based. The executor (`_run_policy_hooks` in `pipeline/anthropic_processor.py`) owns the backend call and stream iteration.
- Policies implement `AnthropicExecutionInterface` (`policy_core/anthropic_execution_interface.py`) with four hooks:
  - `on_anthropic_request(request, context) -> AnthropicRequest`
  - `on_anthropic_response(response, context) -> AnthropicResponse`
  - `on_anthropic_stream_event(event, context) -> list[MessageStreamEvent]`
  - `on_anthropic_stream_complete(context) -> list[AnthropicPolicyEmission]` — where `AnthropicPolicyEmission = AnthropicResponse | MessageStreamEvent`, so tail emissions can include a non-streaming response object, not only stream events.
- Policies never see the IO layer directly. The executor wraps the backend in `_AnthropicPolicyIO` (implementing `AnthropicPolicyIOProtocol`) and calls `io.stream(request)` or `io.complete(request)` exactly once per request based on the incoming `stream` flag.

## Key Patterns (2025-10-24)

- **Event-driven policies**: Policies implement lifecycle hooks (on_chunk_started, on_content_chunk, on_response_completed) instead of callbacks
- **Structured conversation storage**: `conversation_calls` and `conversation_events` tables capture request/response pairs
- **Background persistence**: `SequentialTaskQueue` ensures non-blocking event emission to database
- **OpenTelemetry**: Distributed tracing with automatic span creation and log correlation

## LiteLLM Usage Boundaries (2025-10-17, updated 2026-04-10)

LiteLLM is NOT on the main request path. Anthropic `/v1/messages` traffic is handled by `AnthropicClient` (Anthropic SDK) in `pipeline/anthropic_processor.py`, and the processor never imports `litellm`.

Current imports of `litellm` in `src/luthien_proxy/`:

- `llm/judge_client.py` — wraps `litellm.acompletion` for judge-LLM calls.
- `policies/simple_llm_utils.py` and `policies/tool_call_judge_utils.py` — call `acompletion` directly for judge decisions (these are the underlying utilities for `SimpleLLMPolicy` and `ToolCallJudgePolicy`).
- `main.py` — sets `litellm.drop_params = True` once at startup so judge calls tolerate unknown kwargs.
- `admin/routes.py` — reads `litellm.anthropic_models` to list available Claude models in the admin UI.
- `exceptions.py` — `map_litellm_error_type()` maps litellm exception classes onto `BackendAPIError` codes for judge-path errors.
- `config_fields.py` / `settings.py` — retain `litellm_master_key` as a legacy fallback when resolving judge API keys.

**Key rule**: Do not introduce new LiteLLM imports from gateway request processing, streaming, or the `pipeline/` package. Judge-LLM calls (and the startup/admin touches listed above) are the only supported entry points today.

## E2E Test Infrastructure (2025-10-17, updated 2026-03-25)

Three test tiers with increasing infrastructure requirements:

**sqlite_e2e** (no Docker, fastest): In-process gateway on random port with SQLite.
- Conftest at `tests/luthien_proxy/e2e_tests/sqlite/conftest.py`
- Starts uvicorn in-process, auto-selects free port, auto-teardown
- Run: `uv run pytest -m sqlite_e2e tests/luthien_proxy/e2e_tests/sqlite/ --no-cov -v`
- Must run in **separate pytest session** from mock_e2e (module-level patching)

**mock_e2e** (in-process, deterministic): In-process gateway + mock Anthropic server on dynamic port.
- Mock server at `tests/luthien_proxy/e2e_tests/mock_anthropic/`
- `MockAnthropicServer` enqueues canned responses, `ClaudeCodeSimulator` sends requests
- `policy_context()` fixture hot-swaps policies via admin API
- No Docker needed — `scripts/start_mock_gateway.py` launches everything in-process

**e2e** (Docker + real API): Real Anthropic API calls through Docker Compose gateway.
- Slow, costs money, non-deterministic. Use sparingly.

**Smoke test script**: `scripts/test_gateway.sh` — health, streaming, non-streaming, auth validation.

**Key helpers** (in `tests/luthien_proxy/e2e_tests/conftest.py`):
- `policy_context(class_ref, config)` — set policy, auto-restore NoOp
- `set_policy()` / `get_current_policy()` — admin API wrappers
- `gateway_healthy` — fixture that skips if gateway unreachable

## Streaming Pipeline (2026-04-10)

Anthropic streaming lives inside `src/luthien_proxy/pipeline/anthropic_processor.py`. The executor (`_run_policy_hooks`) owns the backend call: it calls `io.stream(request)` on the `_AnthropicPolicyIO` helper, which invokes `AnthropicClient.stream(...)` (Anthropic SDK), and then iterates the resulting async stream. For each backend event it invokes `policy.on_anthropic_stream_event(event, context)`, which returns zero or more `MessageStreamEvent` values to emit downstream; after the backend stream is exhausted it calls `policy.on_anthropic_stream_complete(context)` to pick up any tail emissions. The processor serializes each emitted event with `_format_sse_event` and yields it to the client as SSE. Policies never see the IO layer directly — they only implement hooks.

Do not trust older notes that describe a `PolicyOrchestrator` + `PolicyExecutor` + `ClientFormatter` + `Queue[ModelResponse]` pipeline or a separate `orchestration/` / `streaming/` package — those modules do not exist in the current codebase. When in doubt, read `src/luthien_proxy/pipeline/anthropic_processor.py` directly; `ARCHITECTURE.md` also describes the flow but has known staleness (tracked separately on Trello).

---

## Admin API for Policy Management (2025-11-20)

The gateway provides an admin API for runtime policy management. Policies are created as named instances and then activated.

### Authentication

All admin endpoints require `Authorization: Bearer ${ADMIN_API_KEY}` header.

### Setting the Active Policy

Use a single endpoint to set the active policy (creates and activates in one step):

```bash
curl -X POST http://localhost:8000/api/admin/policy/set \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}" \
  -d '{
    "policy_class_ref": "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
    "config": {
      "model": "claude-haiku-4-5",
      "probability_threshold": 0.99,
      "temperature": 0.0,
      "max_tokens": 256
    }
  }'
```

### Other Useful Endpoints

- `GET /api/admin/policy/current` - View active policy and its config
- `GET /api/admin/policy/list` - List available policy classes with descriptions

**Files**: `src/luthien_proxy/admin/routes.py`

---

## Don't Overload Fields — Add New Ones (2026-01-28)

**Principle**: When adding a new concept to an existing type, don't make an existing field more complex — add a separate field.

**Example**: Thinking blocks in `AssistantMessage`

- ❌ **PR #134**: Made `content: str | list[dict[str, Any]] | None` — now all code touching `content` must handle both cases, and `Any` loses type safety
- ✅ **PR #138**: Reverted `content` to `str | None`, added separate `thinking_blocks` field — existing code unchanged, new concept is isolated

**Why this matters**:
- Existing code can still make strong assumptions about the original field
- New concept gets its own type with proper validation
- `Any` types are a sign of imprecision — avoid them
- Same complexity, but more modular: logic for the new concept is factored out instead of spread across all field access points

**General rule**: If you're tempted to use `str | list | dict | Any` to handle multiple cases in one field, step back and consider whether each case deserves its own field.

---

## Streaming and Non-Streaming Parity (2026-01-31)

**Principle**: When implementing a feature for streaming responses, ensure the non-streaming path has equivalent behavior (and vice versa).

**Example**: [PR #147](https://github.com/LuthienResearch/luthien-proxy/pull/147) (SimplePolicy non-streaming fix)

- ❌ **Initial fix**: Added `on_response()` that called `simple_on_response_content()` for text — but forgot tool calls
- ✅ **Complete fix**: Also calls `simple_on_response_tool_call()` for each tool call, matching what the streaming path does in `on_tool_call_complete()`

**Why this matters**:
- Users expect consistent behavior regardless of `stream: true/false`
- Easy to forget one path when working on the other
- The streaming and non-streaming code paths are in different methods, so changes don't automatically propagate

**Checklist when modifying response processing**:
- [ ] Does the streaming path handle this? (`on_chunk_received`, `on_content_complete`, `on_tool_call_complete`)
- [ ] Does the non-streaming path handle this? (`on_response`)
- [ ] Are the transformations equivalent?

---

## Anthropic Streaming Lifecycle Parity Pattern (2026-02-27)

- Anthropic streaming now follows the same two-phase lifecycle as OpenAI streaming:
  - `on_anthropic_stream_complete(context)`: normal-completion hook
  - `on_anthropic_streaming_policy_complete(context)`: always-run cleanup hook (even on errors)
- `AnthropicStreamExecutor` mirrors OpenAI semantics:
  - calls `on_anthropic_stream_complete` after successful stream iteration
  - calls `on_anthropic_streaming_policy_complete` in `finally`
- Buffering convention for per-request policy state:
  - use framework-owned typed state: `PolicyContext.get_request_state()` / `pop_request_state()`
  - state `T` should be a dataclass with explicit fields (for strict typing)
  - `PolicyContext` scopes state by `(policy instance, state type)`; per-block maps live inside `T`
  - cleanup via `pop_request_state()` in the always-run cleanup hook
- `PolicyContext` fields available to Anthropic hooks include:
  - `transaction_id`
  - `request` (OpenAI-format request when available)
  - `raw_http_request`
  - `session_id`
  - `scratchpad`
  - `request_summary` / `response_summary` for observability annotations

---

## Database Migrations Lifecycle (2026-03-05)

- Migrations run as a separate Docker service (`migrations` in `docker-compose.yaml`) **before** the gateway starts (gateway depends on `migrations` service).
- On startup, the gateway calls `check_migrations(db_pool)` to validate all migrations have been applied. If any are pending, startup fails with a clear error.
- New tables/columns added by migrations (e.g. `telemetry_config`) are guaranteed to exist by the time application code runs. This means config resolution code can assume tables exist and treat DB errors as transient failures, not missing-schema issues.
- Migration files live in `migrations/` and are auto-discovered by filename sort order (e.g. `009_add_telemetry_config.sql`).

---

## Credential Management Architecture (2026-04-02)

Credentials flow through the system as `Credential` value objects (frozen dataclass in `credentials/credential.py`) carrying a value, `CredentialType` (api_key or auth_token), platform, and optional expiry.

**Request flow**:
```
Request → get_request_credential() → Credential (from headers)
  → verify_token() → validates against proxy key / CredentialManager
  → resolve_anthropic_client() → builds AnthropicClient
  → PolicyContext.user_credential → available to policies
```

- `CredentialType` is determined by transport header: `Authorization: Bearer` → AUTH_TOKEN, `x-api-key` → API_KEY. No prefix heuristics.
- `x-anthropic-api-key` header overrides the forwarding credential (what the backend sees) without affecting auth validation.

**Auth providers** (policy config): Policies declare how to obtain credentials via `auth_provider` in YAML config:
- `user_credentials` (default) — use the request's credential
- `server_key: <name>` — look up operator-provisioned key from `CredentialStore`
- `user_then_server: <name>` — try user creds, fall back to server key (with `on_fallback: fallback|warn|fail`)

**Resolution**: `CredentialManager.resolve(auth_provider, context)` is the single entry point. It dispatches on auth provider type and returns a `Credential`. Server credentials are encrypted at rest (Fernet) and cached in memory with a 60s TTL.

**Admin CRUD**: `POST/GET/DELETE /api/admin/credentials` for server credential management. Names validated against `^[a-zA-Z0-9_-]{1,128}$`.

**Key files**: `credentials/credential.py`, `credentials/auth_provider.py`, `credentials/store.py`, `credential_manager.py`

---

## Conversation History Viewer Architecture (2026-04-08)

The conversation viewer (`/history`) has a **backend extraction layer** and a **frontend presentation pipeline**.

### Backend: `history/service.py` + `history/models.py`

- **Event-based reconstruction**: Turns are built from stored `conversation_events` rows grouped by `call_id`. Each turn combines `transaction.request_recorded` (request) and `transaction.*_response_recorded` (response) events.
- **Dual format support**: Parses both OpenAI-style (`choices[].message`) and Anthropic-style (`role + content blocks`) request/response payloads.
- **Anthropic tool_result extraction**: User messages with `tool_result` content blocks are split into separate `TOOL_RESULT` messages with `tool_call_id` (from `tool_use_id`), enabling frontend pairing.
- **`request_params` allowlist**: A curated set of request parameters (`_REQUEST_PARAM_ALLOWLIST`) is passed to the frontend for turn classification. Uses an **allowlist** (not blocklist) to prevent leaking sensitive fields. `output_config` is sanitized to only pass `format.type`.
- **Preview extraction**: Session titles come from the first non-probe user message. Probes are detected structurally (`max_tokens <= 1`), not by content.

### Frontend: `conversation_live.js`

The frontend has a **presentation pipeline** that transforms raw API turns into display-ready data:

```
Raw turns (from API) → presentTurns() → displayed turns
                         ├── classifyPreflight() — structural classification
                         └── dedup via message slicing
```

- **Dedup**: The API sends cumulative message history each turn. `presentTurns()` slices `request_messages` based on the previous non-preflight turn's message count to show only new content.
- **Preflight classification**: Uses structural request params (not response content): `max_tokens === 1` (quota probe) or `json_schema + max_tokens ≤ 256` (title generation).
- **Tool call/result pairing**: Results are matched to calls by `tool_call_id`. Request tool_calls that duplicate response tool_calls are suppressed.
- **Fingerprinting**: Raw server turn data is fingerprinted (`JSON.stringify`) for incremental DOM updates. The fingerprint must always use raw (not presented) turns to avoid false mismatches.
- **Tagged content**: XML-tagged sections (`<system-reminder>`, `<policy-context>`, etc.) are rendered as collapsible `<details>` blocks.

### Key invariant

The dedup algorithm assumes the API sends a **stable, strictly-growing cumulative message array**. If a policy rewrites earlier messages, the slice offset will be wrong. A `console.warn` fires when the invariant produces empty display messages, but wrong-but-non-empty results are silent.

---

(Add learnings as discovered during development with timestamps: YYYY-MM-DD)
