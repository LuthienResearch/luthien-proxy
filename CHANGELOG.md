# CHANGELOG

## Unreleased | TBA

## 4.0.0 | 2026-09-26

### Breaking Changes

- **Rename `PROXY_API_KEY` → `CLIENT_API_KEY` and reframe auth docs around passthrough as the default.** The gateway's client-facing story is now: Luthien is just an Anthropic endpoint — clients set `ANTHROPIC_BASE_URL` and use their normal `ANTHROPIC_API_KEY` (or Claude Pro/Max OAuth). There is no Luthien-specific key on the client side. The old `PROXY_API_KEY` name leaked a gateway-internal concept into operator docs and confused the mental model; the config field, env var, `AuthMode.PROXY_KEY` → `AuthMode.CLIENT_KEY`, and the `"proxy_key"` auth mode string are all renamed consistently. Observability credential-type markers are also renamed for clarity (`"client_api_key"` → `"user_api_key"`, `"proxy_key_fallback"` → `"client_key_match"`). A new migration (013) rewrites any existing `auth_config.auth_mode = 'proxy_key'` rows to `'client_key'`. Docs across `README.md`, `dev-README.md`, `dev/context/authentication.md`, `docs/standalone-container.md`, `deploy/README.md`, and `src/luthien_cli/README.md` are rewritten to lead with passthrough as the typical default and present `CLIENT_API_KEY` as an optional operator-side feature.

**Deployment notes for operators:**

- **Rename your environment:** replace `PROXY_API_KEY` with `CLIENT_API_KEY` and `AUTH_MODE=proxy_key` with `AUTH_MODE=client_key` before upgrading. **As a safety net for operators who miss this step**, the gateway coerces a legacy `AUTH_MODE=proxy_key` value (from shell env, `.env`, `--auth-mode` CLI flag, or a stale `auth_config` DB row) to `client_key` with a warning so the service stays up. The temporary tolerance will be removed in a follow-up release. A leftover `PROXY_API_KEY` env var emits a similar warning so operators aren't left debugging a silent downgrade to passthrough.
- **Postgres migration ordering:** migration 013 rewrites any existing `auth_config.auth_mode = 'proxy_key'` row to `'client_key'`. SQLite deployments migrate in-process and are safe. On Postgres, migrations run in a separate service — ensure migration 013 has applied before restarting the gateway image. The DB-row tolerance above covers the race window where the gateway restarts before the migration runs.
- **Observability badge:** the nav "API key billing" badge reads new credential-type markers (`"user_api_key"` / `"client_key_match"`). Any in-flight `last_credential_info` entries left over from the pre-upgrade build hold the old marker strings; the badge doesn't match them, so it may briefly read as if no credentials have been seen until the next request flows. `last_credential_info` is in-memory only, so a restart fully clears it. (#535)

### Features

- **`luthien agent-tutorial` and `luthien restart` CLI commands**: `agent-tutorial` prints a tutorial for LLM agents on managing and creating policies. `restart` stops and restarts the gateway in one step. (#511)
- **Automated maintenance autofix now opens one single-concern PR per failing check, with per-concern dedup** (`scripts/automated_maintenance/lib/autofix.sh`)
  - Each failing check (concern) gets its own focused fix session and its own draft PR on `maint-fix/<concern>/<run_id>`, instead of one PR bundling every failure.
  - Before attempting a concern, checks for an already-open autofix PR for that concern and skips it (no duplicate while a fix is in review); a novel concern still gets its own PR. Fails closed (skips) if the GitHub query errors.
  - `results.json` `autofix` is now keyed by concern; the dashboard renders a pill/PR-link per concern and stays backward-compatible with the legacy single-object shape.
- **Automated maintenance pipeline (`scripts/automated_maintenance/`)**: portable scheduled
maintenance for luthien-proxy that runs the check suite, sweeps for doc
drift, optionally autofixes failures, and publishes a static dashboard.

  - Single shell entry point with launchd (macOS) and systemd-user (Linux)
    deploy templates.
  - Headless `claude` integration for doc-drift detection and autonomous
    fix attempts; autofix is opt-in and capped by `AUTOFIX_MAX_BUDGET_USD`.
  - Static HTML dashboard generated per run; point any web server at
    `$MAINT_PUBLIC_DIR`.
- **Before/After preview backend for the admin policy-test endpoint**:
The admin `/api/admin/test/chat` endpoint now returns both `before_content`
(raw LLM output for the original request) and `content` (the active policy's
output for that same exchange). Operators can use the diff to verify a
policy does what they think before activating it on real traffic.
  - The endpoint orchestrates the LLM call and policy hooks in-process and
    no longer makes an HTTP roundtrip to `/v1/messages`. The gateway request
    pipeline is untouched and there is no client-facing protocol opt-in.
  - The test path constructs a full `PolicyContext` matching the one the
    gateway pipeline builds (same emitter, credential manager, policy cache
    factory, raw HTTP request, user credential shape). Judge-style policies
    (LLM judges, `ToolCallJudgePolicy`, `DogfoodSafetyPolicy`, the Block
    presets) now work through the test endpoint exactly as they do for
    real traffic.
  - **Observability**: test-path policy execution emits the same events
    as production runs and appears in the activity monitor. Test sessions
    have ids prefixed `admin-test-session-` so operators can identify or
    filter them.
  - `body.api_key`, when supplied, is now sent directly to Anthropic and
    surfaced to policies as the request's `user_credential` (passthrough
    semantics). Without `body.api_key`, `user_credential` is `None`,
    matching the gateway's client-key-mode semantics.
  - Streaming-only policies appear as no-ops in this preview by design —
    the non-streaming hooks are the source of truth for the test path.
- **Server credentials UI discoverability**: Surfaces the existing server-credential admin API in the UI.
  - Added a `Credentials` entry to the global nav (`static/nav.js`).
  - Added a pointer card on `/config` linking to `/credentials` for operator discoverability.
  - Added a create/list/delete section on `/credentials` wired to `POST|GET|DELETE /api/admin/credentials`.
  - First PR in a series extending Luthien toward server-side inference-provider support.
- **De-AI policy**: New preset that rewrites LLM responses to remove common AI writing patterns (inflated significance, promotional adjectives, overused vocabulary, copula avoidance, em-dash overuse, chatbot artifacts, filler) while preserving technical content. Built on the `SimpleLLMPolicy` judge infrastructure as a comprehensive superset of the No Yapping, No Apologies, and Plain Dashes presets.
- **Reproducible Luthien demo infrastructure**: Adds a generic, manifest-driven demo harness and a first demo (`rm-rf`) showing Luthien blocking a destructive tool call.
  - Per-demo directories at `dev/demo/<name>/` with a `demo.toml` manifest, a `template/` workspace, and a `README.md` narration. New demos plug in without touching shell.
  - `scripts/demo_{setup,toggle,reset}.sh` take `<demo> [state] [surface]` (defaulting to `rm-rf block claude-code`). State = `block` (fabricator + protector) | `dontblock` (fabricator only) | `off` (NoOp). Surface = `claude-code` | `cowork` and picks the fabricated tool name.
  - First demo `rm-rf` includes `DemoForceBashRmRfPolicy` (fabricates a `rm -rf` bash tool_use; tool name configurable per surface) + `BlockDangerousCommandsPolicy` as the protector.
  - Top-level `dev/demo/README.md` covers: how to add a new demo, gateway prereqs, and how to point Claude Code or Cowork (third-party-inference config) at `http://localhost:8000`.
- **Per-key rate limiting on /v1/ routes**: Token bucket rate limiter (in-process, asyncio-safe) applied to all `/v1/` requests. Configurable via `RATE_LIMIT_RPM` (requests per minute, 0=disabled) and `RATE_LIMIT_BURST` (burst size). Returns HTTP 429 with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers when exceeded. (#729)
- **Server-side `InferenceProvider` abstraction**: introduce a named inference
interface for proxy-originated LLM calls (judges, policy-testing, future
proxy-internal inference), with two initial backends.
  - `DirectApiProvider` wraps the existing LiteLLM path; supports a
    `credential_override` for user-credential passthrough.
  - `ClaudeCodeProvider` spawns `claude -p --bare` as a subprocess,
    authenticated by an operator-provisioned OAuth access token, so
    judges can run on a Claude subscription without per-token API billing.
  - Structured output supported on both backends via
    `response_format={"type": "json_schema", "schema": ...}`. The CLI path
    uses `--json-schema` and reads the envelope's `structured_output`
    field; the HTTP path prompt-enforces + validates with `jsonschema`.
    `InferenceResult` now returns `text` plus an optional `structured`
    dict so callers can branch without re-parsing.
  - Cancellation-safe subprocess lifecycle: `CancelledError` from the
    caller now reliably terminates the child `claude` process and reaps
    it before the scratch directory is removed, preventing orphaned
    processes with OAuth tokens in their environment. Resilient to
    repeated `task.cancel()` during cleanup (shield-double-cancel
    footgun closed).
  - `InferenceResult.text` is consistent across providers in structured
    mode: always `json.dumps(structured)`, never raw model-wrapper text.
  - Non-text blocks in `system` message content are now rejected with a
    clear error on both providers (was silently dropped in
    `DirectApiProvider`).
  - Pre-flight JSON-schema validation (both backends) rejects malformed
    or oversized schemas before spending a subprocess spawn or network
    call, and maps to `InferenceStructuredOutputError`.
  - Tightened env-var allowlist for the `claude` subprocess (includes
    `LANG`/`LC_*`/`TMPDIR` so Node locale handling works); name-based
    argv redaction in structured log fields so prompt/schema content
    never leaks to logs.
  - Anthropic-shaped list-of-text-blocks `content` is now handled in both
    providers; non-text blocks raise a clear `InferenceProviderError`.
  - Empty-text guard: a whitespace-only response now raises
    `InferenceProviderError` on both backends instead of silently
    returning success.
  - No callsite changes yet; the registry, YAML rename, and policy-testing
    UI integration come in follow-up PRs. (#605)
- **Inference provider registry**: Added a DB-backed registry for named
`InferenceProvider` instances with admin API and `/inference-providers`
UI, matching the operator workflow already used for server credentials.
  - New `inference_providers` table (postgres + sqlite migration 017).
  - `POST` / `GET` / `DELETE` at `/api/admin/inference-providers`.
  - Providers cached with a 60s TTL, dispatched on `backend_type` via a
    constructor map. Unknown backend types raise a typed error rather
    than failing deep in provider code.
  - `credential_name` is a soft reference — cred deletion surfaces as
    a clear error at `get()` time rather than cascading. (#607)
- **One-click Railway deploy**: Deploy to Railway with a single button click — auto-configures OAuth pass-through, debug logging, and English safety rules. No API keys or database setup needed. (#497)
- **OTLP exporter defaults to HTTP/protobuf**: distributed tracing now exports
via HTTP/protobuf instead of gRPC. HTTP works behind HTTP load balancers
(ALB, nginx, Cloudflare) where gRPC fails with `StatusCode.UNAVAILABLE`.
  - New env var `OTEL_EXPORTER_OTLP_PROTOCOL` (`http/protobuf` default; `grpc` opt-in)
  - Default `OTEL_EXPORTER_OTLP_ENDPOINT` changed from `http://tempo:4317` to
    `http://tempo:4318/v1/traces`
  - `observability/tempo/tempo.yaml` and `docker-compose.yaml` now expose the
    OTLP HTTP receiver on port 4318 alongside the gRPC receiver on 4317
  - Unknown protocol values now raise `ValueError` at startup instead of
    silently falling back, so typos surface immediately
  - **Breaking** for deployments that override `OTEL_EXPORTER_OTLP_ENDPOINT`
    without setting `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`. To preserve the prior
    gRPC behavior set both:

    ```bash
    OTEL_EXPORTER_OTLP_PROTOCOL=grpc
    OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
    ```

Originally proposed in #562 (Sami Jawhar / @sjawhar).
- **Policies page redesign with friendly names, badges, and category grouping**: The `/policy-config` admin UI is now organized around user-friendly metadata instead of raw class names.
  - Adds a `ui` class attribute on `BasePolicy` (a `UIMetadata` frozen dataclass) carrying `display_name`, `short_description`, `category`, `catalog_badges`, and `ui_policy_preview`. `Category` and `CatalogBadge` are typed `StrEnum`s so allowed values are checked at type-check time. The `ui` attribute is structurally isolated from runtime concerns — UI-only, no execution effect.
  - Available column groups policies into four categories (Simple Utilities, Active Monitoring & Editing, Fun & Goofy, Advanced/Debugging) with accordion expand/collapse.
  - Proposed column renders the policy's actual judge prompt directly from `example_config.instructions` (no separate, drift-prone summary field). Blocking policies show a `ui_policy_preview` chip clearly labeled "Policy preview (production output may differ)".
  - Active column hosts the test harness with Before/After comparison.
  - Filter input now matches against display names and short descriptions in addition to raw class names.
- **PolicyCache size cap with FIFO eviction**: `PolicyCache` now enforces an entry cap per policy namespace. When a `put()` would exceed the cap, the oldest entries (by `created_at`) are evicted in the same transaction, so the shared `policy_cache` table no longer grows unbounded between manual `cleanup_expired()` calls.
  - Default cap is 10,000 entries per `policy_name`; configure via `POLICY_CACHE_MAX_ENTRIES` (0 or negative disables the cap).
  - Eviction order: FIFO (`created_at ASC`, with `cache_key` as a deterministic tiebreak). Equivalent to LRU-by-insertion without the read-amplification cost of tracking last-access times.
  - Upsert and eviction run inside one transaction, so a refreshed key is safe from eviction and concurrent puts converge to the cap (soft under Postgres concurrent writers, hard under SQLite's serialized writes).
- **Generic policy cache**: Added a DB-backed key-value cache that any policy can use for persistent, cross-request state. Policies access it via `context.policy_cache("MyPolicy")` and get isolated storage scoped by policy name. Entries have configurable TTL and survive restarts, unlike in-process caches. Works with both Postgres and SQLite deployments.
- **Add `policy_type` registry table**: Catalog of available built-in policy types, decoupled from `current_policy`. Foundation for a future `policy_instance` table.
  - 18-entry explicit `REGISTERED_BUILTINS` allowlist in `policy_types.py`; templates and samples skipped.
  - `sync_policy_types()` is implemented and tested but not wired into the lifespan in this PR — wiring lands with `policy_instance`. (#606)
- **Add /ready readiness probe**: New `GET /ready` endpoint returns 503 when the database is unreachable, times out, or dependencies are not initialized; 200 with `{"status": "ready"}` otherwise. Intended for ECS/k8s readiness probes so traffic is drained when the gateway cannot serve post-startup.
  - DB probe is bounded by `READY_DB_PROBE_TIMEOUT_SECONDS` (2s) via `asyncio.wait_for` so a slow database does not tarpit probe workers.
  - Error reasons are sanitized — no raw exception text or connection details are leaked to unauthenticated callers.
  - `/ready` is included in the no-cache middleware allowlist so CDNs cannot serve a stale cached response.
- **Conversation data retention with optional S3 archival**: Configurable
purge of `conversation_calls` older than `CONVERSATION_RETENTION_DAYS`,
with optional pre-purge archival to S3 (JSONL). Closes #561.
  - `CONVERSATION_RETENTION_DAYS` — purge horizon (disabled by default)
  - `ARCHIVE_S3_BUCKET` / `ARCHIVE_S3_PREFIX` — optional S3 archive target
  - `RETENTION_S3_ENCRYPTION` (`AES256` / `aws:kms` / `bucket-default`)
    and `RETENTION_S3_KMS_KEY_ID` — server-side encryption (validated at
    startup; `aws:kms` requires a customer-managed key id)
  - `RETENTION_ARCHIVE_BATCH_SIZE` — cursor-paginated batch size
  - Background `ConversationPurger` runs once at startup then every 24 h
  - Existing `idx_conversation_calls_created` (from migration 003) already
    serves the purge predicate — no new index introduced
  - **Operator note: archives contain user PII.** Each JSONL line is the
    full conversation record (request/response payloads in
    `conversation_events.payload`, judge prompts and verdicts in
    `conversation_judge_decisions`, policy decisions in `policy_events`).
    Treat the destination bucket as data-at-rest containing user content
    when classifying for compliance, IAM, and replication policies.
  - **Scope: this purger covers `conversation_calls` and the tables that
    cascade off it.** It does **not** cover `request_logs` (the
    HTTP-level table populated when `ENABLE_REQUEST_LOGGING=true`),
    which has no FK to `conversation_calls` and is on a separate
    retention model. If you enable both retention and request logging,
    plan a parallel cleanup for `request_logs` (tracked as a follow-up).
- **Rich infrastructure diagnostics on `/api/admin/system-status`**: A new authenticated admin endpoint probes DB (`SELECT 1`) and Redis (`ping`) in parallel — each bounded by a 2s timeout — and reports per-component status with latency. Overall `status` reflects real infrastructure state: `healthy` / `degraded` (Redis unreachable) / `unhealthy` (DB unreachable); Redis absent (SQLite/local mode) is `not_configured` and does not affect the overall status.

`/health` stays a dependency-free liveness probe (`{status, version}`, always 200) so container/k8s liveness probes don't restart the gateway on a transient DB blip, and `/ready` continues to handle traffic-draining readiness. Keeping the rich checks behind admin auth avoids exposing latency/topology fingerprints — and an unbounded DB/Redis probe — on an unauthenticated endpoint. (#774)
- **Rate limiter follow-ups**: Added `RATE_LIMIT_MAX_KEYS` config field; the limiter now returns a transport-agnostic decision (HTTP translation moved to the route layer); successful `/v1/` responses carry the full `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset` header set (parity with 429 responses); 429 rejections and bucket evictions emit structured warnings; and `dev-README.md` documents the per-process effective-RPM calculation (`RATE_LIMIT_RPM x uvicorn workers x replicas`). (#788)
- **Server-side session search**: `/api/history/sessions` now accepts `model`, `from`, `to`, `q` (full-text content), and `policy_intervention` filters, on top of the existing `user_id`. Resolves #558.
  - Builds on the existing dual-backend FTS infra (Postgres `tsvector` / SQLite FTS5) via `utils.search.session_fts_filter_sql` — no new migration.
  - `q` is porter-stemmed and term-conjunctive with parity across backends; `model`/`q` match if any turn matches, while time and intervention filters operate on session-level aggregates so per-session stats stay whole.
  - `total` in the response now reflects the filtered count (was always the global count). The unfiltered list path is unchanged. (#773)
- **SQLite FTS5 + Postgres tsvector for conversation-event search**: Migration 014
adds Postgres `tsvector`/GIN infra and a SQLite `conversation_events_fts` FTS5
virtual table. Both backends use English stemming (Postgres
`plainto_tsquery('english', ...)`, SQLite FTS5 `tokenize='porter'`) so the same
query returns comparable hits on either dialect. Triggers on
`conversation_events` keep the FTS table in sync on INSERT and DELETE
(including CASCADE deletes from `conversation_calls`).
  - Helper `session_fts_filter_sql(pool, query, *, placeholder)` returns both a
    dialect-correct SQL fragment and a sanitized bind value. On SQLite the
    helper escapes FTS5 meta-characters (``'``, ``-``, ``+``, ``"``, ``:``) by
    quoting each whitespace-separated token as a phrase, preventing MATCH
    syntax errors and matching the conjunction-of-terms semantics of
    `plainto_tsquery`.
  - Fixes the SQLite migration runner to apply trigger-containing migrations
    via native `executescript`, preserving `BEGIN ... END` blocks. Adds a
    startup guard (unit test) that rejects any future SQLite migration file
    using Postgres-only syntax (`$N`, `::type`, `NOW()`, `ILIKE`, `LEAST`,
    `to_timestamp`, etc.) since `executescript` bypasses the runtime
    translator.
- **StringReplacementPolicy request-side filtering**: Added an `apply_to: "request" | "response" | "both"` field to `StringReplacementConfig` (default `"response"`, preserves back-compat). When `apply_to` includes `"request"`, the policy now scrubs incoming user content — string message content, `text` blocks, and `tool_result` content (string and list-of-text-blocks forms) — before forwarding to the backend. `tool_use`, `image`, `thinking`, and the top-level `system` field are not touched. Emits `policy.string_replacement.request_modified` once per request that had any substitutions, mirroring the response-side event payload shape from #693. The hook deep-copies messages before mutation so `original_request` recorded in transaction history remains the user's verbatim input. Closes #557. (#700)
- **StringReplacementPolicy response-side observability**: Replaced the `policy.anthropic_string_replacement.content_transformed` event with `policy.string_replacement.response_modified`, reporting an accurate `total_replacements` count (rather than the configured-pattern count) plus `blocks_modified`, `original_length`, and `transformed_length`. Streaming responses now emit a single aggregated event at stream completion, matching the non-streaming path. Note that for overlap-prone configs (e.g. `[("ab", "ca")]`) the streaming `total_replacements` may differ from the non-streaming count on the same input — streaming reports substitutions actually performed at chunk boundaries, while non-streaming counts substitutions over the fully-assembled text. **Breaking for observability consumers**: any dashboards, alerts, or queries keyed on the old event name must be updated to the new name. (#693)
- **Unified config system**: All gateway configuration defined in one place (`config_fields.py`) with layered resolution (CLI > env > DB > defaults) and provenance tracking. New `/config` dashboard shows where every value comes from. Admin API endpoints for viewing and editing config at runtime. CLI flags auto-generated for all settings. `.env.example` auto-generated from config spec.
- **Configurable upstream header injection**: New `UPSTREAM_HEADERS` environment variable (JSON) lets the operator inject custom headers into upstream LLM API requests with per-request template expansion (`${session_id}`, `${request_path}`, `${env.VARNAME}`). Primary use case: chaining Luthien in front of Helicone or other LLM observability proxies that require session tracking and analytics headers. Misconfiguration (invalid JSON, malformed RFC 7230 header names, hop-by-hop headers, non-string values) fails the gateway at startup rather than silently disabling the integration. Replaces the previously rejected PR #549; original commits authored by sjawhar are preserved. (#716)
- **User differentiation in the history viewer**: Attribute and label traffic per user on shared deployments.
  - `request_logs` now carries `user_id` (mirroring `conversation_calls`), with a `user_id` filter on the request-logs API and UI.
  - New `session_summaries` materialized table, maintained incrementally on each event write (counts, models used, message preview, attributed `user_id`), so the history list does not re-aggregate `conversation_events`.
  - New `user_labels` table mapping `user_id` to a display name, with `/api/history/users` and `/api/history/user-labels` endpoints (list / set / delete).
  - History UI: per-user filter dropdown, deterministic-colored user badges on session cards, and click-a-badge to assign or clear a display name.
- **User identity extraction**: The proxy now records a `user_id` against
each conversation call so operators can attribute traffic to individual
users in the history UI and `GET /api/history/sessions?user_id=...`.

  - Source 1 (default off): `X-Luthien-User-Id` request header. Trusted
    only when `TRUST_USER_ID_HEADER=true` — leave disabled unless clients
    are behind an authenticated reverse proxy.
  - Source 2: the `sub` claim of a Bearer JWT, decoded **without signature
    verification**. Treat as user-asserted attribution, never as auth.
  - Stored in a new column on `conversation_calls` (migration 018), and
    surfaced on `SessionSummary.user_ids` (a list, so sessions reused across
    users render honestly instead of attributing to one). Retention/purge
    tooling that scrubs PII should account for this column.
  - Also captured as a `luthien.user_id` OpenTelemetry span attribute and
    logged at DEBUG (`Extracted user_id: ...`). For deployments where
    `user_id` is an email or other PII, those sinks see it too — flip
    `TRUST_USER_ID_HEADER` and/or accept JWT Bearers only with that in mind.
- **Webhook event export**: Fire-and-forget POST to `WEBHOOK_URL` on conversation event completion (streaming + non-streaming) with session/transaction/model/usage/duration payload, plus `success`, `http_status`, and Anthropic prompt-cache token counts.
  - Configurable: `WEBHOOK_URL`, `WEBHOOK_MAX_RETRIES`, `WEBHOOK_RETRY_DELAY_SECONDS`, `WEBHOOK_MAX_PENDING_TASKS`, `WEBHOOK_SHUTDOWN_DRAIN_SECONDS`
  - Disabled when `WEBHOOK_URL` is empty
  - **Scope**: fires only on `/v1/messages` conversation completion; not wired into the `/v1/{path:path}` passthrough (count_tokens, models, etc. are not "completions")
  - Singleton `httpx.AsyncClient`, exponential backoff with jitter (capped at 60s), bounded pending-task pool, bounded drain on shutdown
  - URL sanitization in logs (full path redacted, userinfo stripped, IPv6 bracketed); rejects non-HTTP(S) schemes at construction
  - Backpressure observability: `pending_depth` / `dropped_count` / `max_pending_tasks` properties; admin endpoint `GET /api/admin/webhook/stats`
  - Streaming webhook is suppressed on bare client-disconnect (no false-success); fires with `success=False` on policy errors and empty streams
  - Non-streaming webhook fires with `success=False`, `http_status=<error code>` on policy/backend errors — symmetric with streaming
  - **`duration_ms` semantics**: non-streaming = request-received → response-ready; streaming = request-received → generator's finally (includes client-drain time, so slow consumers inflate the number). The two are not the same measurement.
  - **`success` semantics**: `success=True` means the gateway built and dispatched a response, not that the client received it. Both stream and non-stream fire from finally blocks before the response leaves the gateway. For at-least-once delivery confirmation, use the durable Postgres event recorder.
  - **`success` reflects gateway-side outcome only**, not upstream content. If Anthropic emits an `error`-typed event mid-stream and the policy passes it through, the webhook fires with `success=True` because the gateway successfully streamed an error event to the client. Receivers building reliability dashboards from `success` should also inspect upstream content if they care about Anthropic-side errors.
  - **Non-standard `http_status` codes**: cancellation surfaces as `499` (Client Closed Request — nginx convention, not in RFC 7231). Receivers indexing `http_status` as a categorical field should handle non-RFC values gracefully.
  - **Schema policy** (`schema_version: 1`): additive fields don't bump the version; renames, removals, and semantic shifts do. Receivers should ignore unknown fields and version-gate any field they treat as load-bearing.
  - **`total_tokens` excludes cache tokens** (`= input_tokens + output_tokens` only). Anthropic bills cache writes at 1.25× and reads at 0.1× — naive summation would mislead spend dashboards. Cache tokens are surfaced separately as `cache_creation_input_tokens` / `cache_read_input_tokens` so consumers can weight them per their billing model.
  - **At-most-once delivery**: failures after retries are dropped, shutdown drains then cancels, process crashes lose in-flight events. Not suitable for systems that require at-least-once or durable delivery. (#741)

### Fixes

- **SimpleLLMPolicy: response composition lifted into AnthropicMessageBuilder; tool_use-trailing invariant enforced by construction** (#708)
  - Previously, when the judge was unreachable and `on_error="pass"`, the
    judge-unavailable warning text block was appended after any emitted
    `tool_use`. On the next turn the Anthropic API rejected the conversation
    with `messages.X: tool_use ids were found without tool_result blocks
    immediately after`, bricking the session (`API Error: 400 due to tool
    use concurrency issues`).
  - Yash hit this on the RealPage 2026-05-06 trial. Empirically verified
    against the live Anthropic API on 2026-05-13 (see
    `tests/luthien_proxy/e2e_tests/real_anthropic/probe_tool_use_invariant.py`
    for the case matrix).
  - Root cause was structural: the policy was composing the wire response
    inline at four scattered emission sites, each having to remember to
    enforce the trailing-tool_use invariant. Multiple paths (replacement
    creating a tool_use, late judge failures, multi-block sequences) could
    silently violate it.
  - **Fix**: introduce `policy_core.AnthropicMessageBuilder` which owns
    Anthropic-streaming concerns end-to-end — upstream block buffering,
    downstream wire composition, index allocation, the trailing-tool_use
    invariant. `tool_use` decisions buffer until `finalize()`; text and
    passthrough blocks emit immediately if no tool has been buffered yet,
    otherwise queue for emission *before* the tool flush. The wire
    invariant is true by construction; it cannot be violated regardless of
    when warnings or markers are noted relative to the tool stream.
  - SimpleLLMPolicy is now thin: dispatch each upstream event to the
    builder, judge complete blocks, register decisions. The state struct
    drops `tool_use_emitted`, `tool_blocking_engaged`, `index_shift`,
    `emitted_blocks`, `warning_emitted`, the upstream `text_buffer` and
    `tool_buffer` — all subsumed by the builder.
  - **Behavior change**: subsequent tools after a `block` decision are now
    judged independently rather than silently dropped. The consolidated
    blocked-tools marker emits in the pre-tool slot at finalize, so a
    blocked tool no longer prevents a passing tool in the same response
    from going through. Each tool is judged once.
  - **Behavior change**: text blocks that arrive after a tool_use in the
    upstream stream are now reordered into the pre-tool region rather than
    dropped. Both blocks are preserved on the wire.
  - Test harness: `ClaudeCodeSimulator` now preserves wire-order block
    layout when reconstructing assistant content. The previous behavior —
    merging text blocks and grouping `tool_use` after text — silently
    corrected malformed proxy output before it reached turn 2, hiding this
    class of bug.
- **Activity stream sqlite e2e fixture settings cache**: Follow-up to PR #538. The `gateway_url` module-scoped fixture in `tests/luthien_proxy/e2e_tests/sqlite/test_activity_stream.py` had the same stale-settings-cache bug: it set `ANTHROPIC_*` env vars then called `create_app()` without flushing `get_settings()`, and module-scoped fixtures run before the function-scoped autouse cache clearer. Now calls `clear_settings_cache()` before `create_app()` and again in teardown. Also added a teardown `clear_settings_cache()` to `sqlite/conftest.py::sqlite_gateway_url` for full hermeticity.
- **Admin auth documentation correctness**: Corrected `dev-README.md`, `dev/context/codebase_learnings.md`, and `dev/context/gotchas.md` which all claimed admin endpoints unconditionally required `Authorization: Bearer ADMIN_API_KEY`. In reality, `LOCALHOST_AUTH_BYPASS` (enabled by default) covers admin routes too — this was intentional (PR #405) but the docs were never updated. Also clarified the `LOCALHOST_AUTH_BYPASS` config description and enumerated the accepted admin credentials (Bearer, `x-api-key`, session cookie).
- **Fix stored-XSS class in admin UI static assets**: A comprehensive sweep of
`src/luthien_proxy/static/**`. Attacker-influenced values (session_id, call_id,
transaction_id, tool_call_id, key_hash, credential/model/endpoint/provider
names, full activity-stream event payloads, server error messages) were
interpolated into inline `onclick` JS strings, quoted HTML attributes, or HTML
text — some via hand-rolled escapers that did not escape `'`/`"`, some (in
`credentials.html`) via raw string concatenation with no escaping at all, some
(in `activity_monitor.js`) via `JSON.stringify(event)` straight into a `<pre>` —
allowing breakout and script execution.
  - Migrated the attacker-controlled JS-string / event-handler / HTML-text sinks
    in `history_list.html`, `diff_viewer.html`, `request_logs.html`,
    `inference_providers.html`, `credentials.html`, `config_dashboard.html`, and
    `activity_monitor.js` to DOM construction (`createElement` / `textContent` /
    `addEventListener` / `dataset`) — markup and quotes are inert by
    construction, no new dependency.
  - Hardened the retained escapers (`escapeHtml` in `conversation_live.js` /
    `diff_viewer.html`, `esc` in `config_dashboard.html`) to escape all five
    HTML-significant characters so attribute interpolation cannot break out.
  - Converted `e.message` → `innerHTML` error sinks (unconstrained server text)
    to `textContent` across the affected files.
  - Added source-level regression guards in
    `tests/luthien_proxy/unit_tests/ui/test_static_xss_guards.py`.
- **luthien-cli build failure on recent checkouts**: Filter `git describe` to only consider `cli-v*` tags when resolving the CLI version. Previously, after the proxy's `v3.0.0` tag landed, any fresh checkout or worktree would fail to build with `UserWarning: tag 'v3.0.0' no version found` because hatch-vcs picked the nearest tag (`v3.0.0`) before the cli-specific `tag_regex` could filter it. (#520)
- **Config DELETE no longer reverts to a ghost ENV layer after PUT**: `ConfigRegistry._resolve_field` used to re-derive the ENV layer on every resolve by comparing `settings.<field>` to `meta.default`. Because `set_db_value` writes the coerced value back into `Settings` via `_sync_one`, any field that received a DB override would be reported as ENV-sourced on later resolves, and a subsequent `DELETE /api/admin/config/{key}` would "stick" at the DB value instead of falling back to the real default. The ENV layer is now snapshotted once at `__init__` and consulted from that immutable map.
- **Enum defaults in generated `.env.example`**: `AuthMode(str, Enum)` fields rendered as `AuthMode.BOTH` instead of `both` because `Enum.__str__` wins over `str.__str__`. The generator now explicitly unwraps `Enum` defaults via `.value`, and a regression test guards the behavior. (#534)
- **Automated maintenance: fix stray-brace bug that silently dropped every check result** (`scripts/automated_maintenance/lib/config.sh`)
  - `maint_record_check`'s `extra="${4:-{}}"` default parsed as `${4:-{}` plus a literal `}`, appending a stray brace to every payload. `json.loads(extra)` then failed with "Extra data", so no check was ever recorded — every run produced `checks: {}` and `overall: unknown`, leaving the dashboard blank.
  - Added a subprocess-driven regression test (`test_record_check.py`) that exercises the real bash function.
- **History session previews now reflect user input, not gateway-injected content**:
  `_extract_preview_message` now reads from `original_request` instead of
  `final_request`, so previews show the actual user message even when
  `inject_policy_awareness_anthropic` (or any future injector) prepends content
  to the first user message. Falls back to `final_request` for older payloads.
- **dev_checks: unit tests no longer hang or error**:
  - `tests/luthien_proxy/unit_tests/inference/test_registry.py` fixture used a naive `sql.split(";")` to apply migrations, which shredded trigger bodies in `014_add_session_search_fts.sql` and failed with `near "the": syntax error`. Now reuses `_apply_sqlite_migrations()` (same runner used in production), which understands `BEGIN…END` blocks via `executescript()`.
  - `tests/luthien_cli/test_claude.py::test_claude_fails_when_not_installed` was not patching `ensure_gateway_up`, so it polled the real gateway health endpoint until pytest's timeout — making the unit-test phase appear to hang. Now patched consistently with the other tests in the file.
- **Restore `.env.example` and add regression guard**: The file was accidentally wiped to zero lines in PR #519 (commit `1b28e4d5`), leaving main's CI dev_checks job red because the clean-tree gate caught the generator producing 150 lines of output on every run. Regenerated the committed copy from `scripts/generate_env_example.py` and added a unit test (`test_env_example_matches_generator`) that asserts the committed file matches the generator's output. This test runs in the fast unit tier and also guards against the original "hardcoded SERVICE_VERSION=2.0.0" class of bug — any config field whose committed default drifts from `config_fields.py` will now be caught before CI. (#527)
- **SimpleLLMPolicy: emit replacement blocks at monotonically increasing indices**: When a judge replaced one upstream content block with multiple blocks (N>1), all replacement blocks were emitted at the same index, producing an invalid Anthropic stream. Fixed by tracking `index_shift` on per-request state; replacement now emits at sequential indices and subsequent passthrough blocks are shifted to avoid collisions.
- **Fix SQLite translator mishandling of positional arg reuse**: queries that reuse
a single `$N` placeholder (e.g. `VALUES (..., $8, $8)`) — valid on
asyncpg/Postgres — now work against the SQLite backend. Unblocks
`POST /api/admin/credentials` and any future positional-reuse call sites on
dockerless dev setups. (#600)
- **Stop leaking auth-mode and credential activity from `/health`**: The unauthenticated `/health` endpoint previously returned `auth_mode`, `last_credential_type`, and `last_credential_at`, which a probe attacker could use to fingerprint the gateway's auth configuration and recent credential activity. Those fields now live on a new authenticated endpoint, `GET /api/admin/billing-status`, and the admin UI's nav badge fetches from there. `/health` is now `{status, version}` only.
- **Judge client: cached HTTP client + skip retries on permanent failures**
  - `DirectApiProvider` now reuses a cached HTTP client for stable-credential judge calls (via `anthropic_client_cache`) instead of building and closing a fresh client on every call. A judge that fires on every tool call in an agent loop no longer pays connection-pool init/teardown each time. Per-user passthrough (with `credential_override`) still builds and closes a fresh client per call.
  - `call_simple_llm_judge` no longer retries permanent failures: a rejected credential (`InferenceInvalidCredentialError`, 401/403) fails fast instead of burning `max_retries * retry_delay` seconds and hammering the upstream. Transient errors and parse failures still retry.
- **Security: localhost auth bypass refuses proxied requests**: the localhost auth bypass no longer applies to requests that carry reverse-proxy forwarding headers (`X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `X-Forwarded-Host`, `X-Forwarded-Proto`), even when the TCP source address is loopback. A same-host reverse proxy (Caddy, nginx, Traefik) previously made every external request look like 127.0.0.1 and exposed the admin/history/debug surface unauthenticated. Direct loopback requests (local curl, dockerless dev, `luthien` CLI) still bypass as before. The gateway also logs a startup warning whenever the bypass is enabled.
- **Mock Anthropic server auto-allocates a free port by default**: The
`mock_anthropic` test fixture now picks an unused TCP port from the OS
when `MOCK_ANTHROPIC_PORT` is unset, instead of always trying 18888.
This unblocks running e2e suites alongside other stacks (dev environments,
parallel CI shards) without manual port juggling. Set `MOCK_ANTHROPIC_PORT`
to a specific value to keep the old fixed-port behaviour.
- **`derive_builtin_name` splits acronym-then-word boundaries**: a policy class
name like `HTTPSRedirectPolicy` now derives to `https-redirect` instead of
`httpsredirect`. No-op for current `REGISTERED_BUILTINS`; only affects future
acronym-prefixed names.
- **FTS backfill computes search text once per row**: The Postgres `014_add_session_search_fts.sql` backfill called `_extract_event_search_text(payload)` twice per row (once in the `UPDATE SET` clause, once in the `WHERE` filter), doubling the per-row work over the whole `conversation_events` table. It now computes the value once in a subquery and reuses it for both the tsvector and the NULL filter; results are unchanged. (PR #614 review follow-up, GH #763)
- **E2E suite: fix UAT mock tests and unblock real tier**:
  - Fix `test_mock_uat01_onboarding_context.py`: format `_WELCOME_SETUP_HINT` with `gateway_url` before substring check (the unformatted template literal `{gateway_url}` could never appear in rendered output).
  - Fix `test_mock_uat03_unimplemented_policies.py`: assert `200 {success: false, error}` matches the actual `/api/admin/policy/set` contract for unloadable policies, not 400/422; correct `_ADMIN_POLICY_GET_PATH` to `/api/admin/policy/current`.
  - Fix `test_mock_uat04_api_key_errors.py::test_wrong_admin_key_returns_clear_error`: toggle `LOCALHOST_AUTH_BYPASS` off via the config API for the duration of the test so wrong-key auth is actually exercised.
  - Fix `test_mock_uat05_policy_stability.py`: same admin-policy GET path fix as UAT03.
  - Fix Docker boot on real tier: set `UV_NO_SYNC=1` on the gateway service so `uv run` doesn't re-sync at runtime — that re-sync invokes hatch-vcs, which writes `_version.py` into the read-only `./src` mount.
  - Don't gate the entire real tier on `ANTHROPIC_API_KEY`: judge-policy tests already self-skip when missing, and the rest of the tier works via OAuth/passthrough.
- **Fix Railway one-click deploy button**: Replace generic GitHub-URL deploy link with a published Railway template that deploys a minimal proxy-only service (SQLite, passthrough auth, no API keys required)
- **Admin UI fails closed when `ADMIN_API_KEY` is unset**: `check_auth_or_redirect` previously returned `None` ("authenticated") when no admin key was configured, so the admin/history UI routes (`/history`, `/config`, `/credentials`, …) were served without authentication. They now redirect to login (deny), matching the admin API path (`verify_admin_token`, which already rejects when the key is unset). Localhost-bypass is unchanged, so dockerless local dev still works. (Supersedes the role-separation approach explored in #574/#772.)
- **Sqlite e2e boot helper setup-time leak**: `boot_sqlite_gateway` (added in #541) only ran cleanup if execution reached `yield` — a raise from `check_migrations()`, `create_app()`, or the 10-second gateway-startup wait would leak the tmp_dir, env-var modifications, settings cache state, and (for the startup-wait case) the uvicorn thread. Rewrote the helper to use `contextlib.ExitStack`, registering each rollback as the matching resource is acquired, so any setup failure tears down only what was actually set up. Pre-existing in both pre-#541 fixture copies; centralizing them made it fixable in one place. Added regression tests in `tests/luthien_proxy/e2e_tests/sqlite/test_boot_helper.py` covering the `create_app` and `check_migrations` failure paths.
- **AnthropicMessageBuilder: stop_reason rewrite is now conservative; diagnostic upstream reasons preserved**
  - Previously the builder unconditionally rewrote `stop_reason` to
    `tool_use` or `end_turn` based on whether any tool was actually
    emitted. That clobbered legitimate upstream `max_tokens`,
    `stop_sequence`, `refusal`, and `pause_turn` values, masking
    information clients depend on (e.g. a `max_tokens` truncation
    looked like a normal `end_turn` to the client and never triggered
    continuation logic).
  - **Fix**: only rewrite when upstream is *known wrong* given what we
    emitted — specifically, `stop_reason == "tool_use"` with no tool
    on the wire becomes `end_turn`. Every other reason is preserved
    verbatim. Mirrors the conservative shape established in PR #721.
  - Affects all Anthropic streaming and non-streaming policies that
    route through `AnthropicMessageBuilder` (SimpleLLM, DogfoodSafety,
    ToolCallJudge). (#754)
- **Friendly summaries for string replacement events**: The dashboard activity stream now renders human-readable text for `policy.string_replacement.request_modified` and `policy.string_replacement.response_modified` events instead of the raw event-type string.
- **ToolCallJudgePolicy streaming stop_reason**: When every `tool_use` block in a streaming response was blocked by the judge, the terminal `message_delta` still reported `stop_reason="tool_use"`, causing Claude Code (and other Anthropic SDK consumers) to abort with `"The model's tool call could not be parsed (retry also failed)."`. The streaming path now rewrites `stop_reason` to `"end_turn"`, mirroring the existing behavior of the non-streaming path. (#721)

### Refactors

- **ConversationLinkPolicy uses Pydantic config**: Constructor now accepts `ConversationLinkPolicyConfig` (matching the rest of the codebase) instead of a scalar `base_url` kwarg, making the admin UI render it via the Pydantic form path.
  - Removed the legacy JS config-form renderer (`renderLegacyConfigFormInner` / `bindLegacyConfigInputs`) from `policy_config.js` — `ConversationLinkPolicy` was its last consumer.
- **Migrate judges to InferenceProvider, retire LiteLLM**:
  - Policy YAML field `auth_provider:` renamed to `inference_provider:`; old name still parses and logs a deprecation warning.
  - Judge policies (`SimpleLLMPolicy`, `ToolCallJudgePolicy`) now resolve their inference target through `luthien_proxy.inference.dispatch.resolve_inference_provider`, which dispatches on `UserCredentials` / `Provider(name)` / `UserThenProvider(name, on_fallback)`.
  - `DirectApiProvider` swapped from LiteLLM to the Anthropic SDK; structured outputs now use Anthropic's native tool-use (single forced tool with the caller-supplied schema).
  - Removed `litellm` dependency, `LITELLM_MASTER_KEY` / `LLM_JUDGE_API_KEY` config fields, `map_litellm_error_type`, and `luthien_proxy/llm/judge_client.py`.
  - `boto3` (used by the opt-in S3 conversation archiver) previously arrived transitively via `litellm[proxy]`. It is now a direct dependency, so archiving keeps working for every install with no operator action. (#776)
- **Move test-only PolicyContext factory out of production code**: moved the test-only `PolicyContext.for_testing()` factory out of the production
`policy_core` module into a test fixture (`make_policy_context()` in
`tests/luthien_proxy/fixtures/policy_context.py`). No user-facing behavior change.
- **Remove deprecated `/api/admin/gateway/settings` endpoint**: The policy config UI now reads/writes `inject_policy_context` and `dogfood_mode` via the canonical `/api/admin/config` and `/api/admin/config/{key}` endpoints. The superseded `GET`/`PUT /api/admin/gateway/settings` handlers have been deleted.
- **policy_cache: drop unused `updated_at` column**: The `updated_at` column on the `policy_cache` table was written by every upsert but never read by any code path. Dropped it from the migration files and the `put()` SQL to avoid write amplification and lock in the YAGNI decision. Added schema regression tests that will fail if the column (or a SQL statement referencing it) is reintroduced without a consumer.
- **Require `auth_provider` for judge-using policies**: `SimpleLLMPolicy` and
`ToolCallJudgePolicy` now require an explicit `auth_provider` in their config.
The legacy per-policy `api_key` field and implicit passthrough/env-key fallback
("Step 5b" code path) have been removed. Shipped configs
(`config/railway_policy_config.yaml`, `config/policy_config.yaml`) and all
bundled presets declare `auth_provider: "user_credentials"` to preserve
existing OAuth-passthrough behavior. `call_judge()` from
`tool_call_judge_utils` and the `_extract_passthrough_key` / `_resolve_judge_api_key`
helpers on `BasePolicy` are gone.
- **Sqlite e2e gateway boot helper**: Extracted the in-process SQLite gateway boot/teardown logic into `tests/luthien_proxy/e2e_tests/sqlite/_boot.py::boot_sqlite_gateway`. Both `sqlite/conftest.py::sqlite_gateway_url` (session-scoped) and `sqlite/test_activity_stream.py::gateway_url` (module-scoped) now share the same code path — eliminating ~140 lines of near-identical scaffolding and the drift risk that caused #539 (a stale-settings-cache fix that had to be applied separately to each copy after #538 only patched one). Also closes a pre-existing `tmp_dir` leak in `test_activity_stream.py` teardown that the conftest copy already handled.
- **Extract `ToolCallStreamBuffer`**: policy-agnostic Anthropic streaming filter parameterized by a caller-supplied async transform closure. `DogfoodSafetyPolicy` and `ToolCallJudgePolicy` now define only a transform closure; per-request state, output indices, and `stop_reason` invariants are owned by the buffer. Replaces #728's per-event-helper extraction (which dropped the `message_delta` `stop_reason` rewrite). Tool-call decisions now run at `message_delta` with the full list of buffered tool calls, instead of per-block at `content_block_stop`. (#748)

### Chores & Docs

- **Cleanup stale LiteLLM content in dev/context/ and dev/REQUEST_PROCESSING_ARCHITECTURE.md**: the agent-facing context docs still described a LiteLLM-backed gateway path that was replaced by direct Anthropic SDK usage. Rewrote the architecture overview, streaming-pipeline notes, and thinking-block gotchas to reflect the current `pipeline/anthropic_processor.py` + `AnthropicClient` path, and scoped the remaining LiteLLM references to the judge-LLM path (`llm/judge_client.py`, `simple_llm_utils.py`, `tool_call_judge_utils.py`) where it is still used.
  - Also regenerated `.env.example` as a bootstrap fix: commit 1b28e4d5 had truncated it to 0 bytes, which broke the `dev_checks.sh` clean-tree gate on every branch off main. Included here so this docs PR could pass its own CI. (#532)
- **Regression suite for known-bad API request patterns (COE audit)**: Adds 19 unit tests pinning how the transparency-first pipeline handles the request patterns that caused production 400s in the LiteLLM era (empty text blocks PR #201, orphaned tool_results PR #167, cache_control extra fields PR #178, context_management PR #151, duplicate tools, parallel tool_use ordering PR #356). Verified against the live Anthropic API on 2026-07-06: bad patterns are forwarded verbatim and upstream 400s are relayed cleanly; context_management is now a real API feature that must be forwarded, and whitespace-only text blocks are now accepted upstream.
- **DatabasePool test construction idiom**: Documented the canonical pattern for tests that need an in-memory SQLite `DatabasePool` with pre-populated schema (construct via the public constructor, prime with `get_pool()`, seed schema on the returned pool). Added a regression test in `tests/luthien_proxy/unit_tests/utils/test_db.py` that pins the pattern so downstream tests don't reach for `DatabasePool.__new__(...)` + private-attribute pokes.
- **dev_checks: `--skip-reports` / `--fast` inner-loop mode**: Added `--skip-reports` flag that skips report-only steps (ruff docstrings, radon) and pytest coverage. Gating checks (ruff, pyright, pytest) still run. `--fast` is an alias that may enable more shortcuts in the future. Saves ~13s on a typical warm run (~45s → ~32s). Use while iterating; run the full gate before pushing.
- **dev_checks: concurrent pyright + pytest**: In Phase 2, pyright and pytest now run in parallel (they're independent). Output is captured to separate logs and surfaced sequentially after both complete. Saves ~9-10s on a typical warm run (~54s → ~44s).
- **dev_checks: per-step timing instrumentation**: Added `--timing` / `--timing=PATH` flag to `scripts/dev_checks.sh` that writes one JSON line per step to `.dev_checks_timings.jsonl` (run_id, step, duration_s, exit_code, ts) and prints a sorted summary at the end. Added a `Testing & QA` section to `dev-README.md` documenting test tiers, dev_checks flags, and performance characteristics.
- **dev_checks: parallel pytest workers (xdist, default 4)**: `scripts/dev_checks.sh` now runs pytest with `-n 4` by default via `pytest-xdist`. Saves ~12s on the full gate (coverage instrumentation is CPU-bound and parallelizes well) and ~4s in `--skip-reports` mode. Override via `--workers=N` flag or `DEV_CHECKS_PYTEST_WORKERS` env var; use `--workers=1` to disable for debugging flakes or interleaved output.
- **Delete stale/unreferenced `dev/*.md` files, move accurate architecture doc to `dev/context/`**: Removed `dev/LIVE_POLICY_DEMO.md`, `dev/OBSERVABILITY_DEMO.md`, `dev/observability.md`, `dev/VIEWING_TRACES_GUIDE.md`, `dev/success.md`, `dev/plans/*.md`, and `dev/user-stories/` (all stale, superseded, or no longer in use). Moved `dev/REQUEST_PROCESSING_ARCHITECTURE.md` → `dev/context/request_processing.md` — it remains accurate and belongs with other developer-internals docs. Updated `dev-README.md` cross-references accordingly. Leaves `dev/` root clean with only the three documented subdirs (`scratch/`, `context/`, `archive/`).
- **Move planning scratch to `dev/scratch/` (gitignored)**: `dev/OBJECTIVE.md`, `dev/NOTES.md`, and in-flight design plans now live in gitignored `dev/scratch/` rather than tracked at `dev/` root. Objective Workflow updated: the objective-setting commit is now `git commit --allow-empty` (the message feeds `gh pr create --draft --fill`) so no artifact needs committing at the start. `dev/context/` and `dev/archive/` remain tracked as before. Motivation: prevent planning drafts from leaking into feature commits (e.g. commit 540e2825 bundled 1084 lines of scratch).
- **Sync docs after teardown sweep**: Drop stale references to removed code from `ARCHITECTURE.md` and `dev/` context docs — `/activity/monitor` and `/debug/diff` redirects (removed in #597), the `gateway/settings` admin endpoint (removed in #602), and the `litellm_master_key` judge-key fallback (removed in #603).
- **Remove e2e tests that misuse Bearer auth with API keys**: Deleted three passthrough-auth tests and one streaming-chunk test that sent `Authorization: Bearer <sk-ant-...>`. Anthropic only accepts API keys via `x-api-key`; Bearer is reserved for OAuth, whose automated use Anthropic forbids. Replaced the broken probe fixture with `gateway_passthrough_mode`, which reads `auth_mode` from the admin API. The buffered tool-call streaming behavior remains covered by `mock_e2e` tests. (#747)
- **Consolidate dev docs**: Make `dev-README.md` the canonical development guide, deduplicate `CLAUDE.md`, delete the stale `dev/README.md` navigation index, and rewrite the releasing section to document the auto-tag workflow. Also fixes several inaccuracies surfaced during review (observability defaults, auth layers, deployment modes, e2e commands, billing warning).

**Fix stale SERVICE_VERSION**: `service_version` now derives from `luthien_proxy.version.PROXY_VERSION` (package metadata) instead of a hardcoded `"2.0.0"` relic. **Operational note**: Sentry `release` tags and OTel `service.version` resource attributes will change shape from `luthien-proxy@2.0.0` to the actual package version (e.g. `0.1.20.dev2+g64a517c2`). Dashboards or alerts filtering on the old value will need to be updated. (#514)
- **Rewrite live-view e2e tests against native Anthropic shape**: `tests/luthien_proxy/e2e_tests/test_conversation_live_view.py` was wholly written against the old OpenAI/LiteLLM response shape and failed at the first response-shape assertion against the current native-Anthropic gateway. Rewritten as `test_mock_conversation_live_view.py` on the `mock_e2e` tier (no real API calls) using the same template as PR #717's history-tests rewrite.
  - Add `_wait_for_session(...)` polling helper to `tests/luthien_proxy/e2e_tests/conftest.py` (replaces fixed `asyncio.sleep` waits in mock_e2e tests; usable by both files going forward).
  - Delete the old e2e file (not skipped — same as #717's treatment). (#722)
- **Mock e2e test suite (UAT01-05)**: Adds 20 `mock_e2e` tests covering onboarding context injection, de-slop policy activation (NoYappingPolicy, NoApologiesPolicy, PlainDashesPolicy), unimplemented policy error handling, API key error message format, and policy setup stability under rapid switching and batch load. (#502)
- **Migration naming guard**: Added `test_migration_naming.py` enforcing migration filename hygiene in the default test/`dev_checks` pass — `NNN_snake_case.sql` format, no *new* duplicate numeric prefixes (existing `008`/`014` collisions grandfathered, since the filename-keyed `_migrations` table makes renumbering applied history unsafe), and Postgres/SQLite prefix parity. Prevents recurrence of the silent prefix collisions where two branches grabbed the same migration number. (#778)
- **Policy authoring skill**: Added a Claude Code skill at `.claude/skills/policy-authoring/` with a comprehensive guide to writing Luthien policies — base class selection, lifecycle hooks, streaming gotchas, request-scoped state, and working examples. (#518)
- **Fix flaky `test_new_key_is_never_immediately_self_evicted`**: The test used `ttl_seconds=1` to demonstrate "short TTL" but SQLite stores `expires_at` at second precision (`datetime('now')` truncates fractional seconds), so up to ~1 full second of the TTL could vanish before the assertion. On loaded CI runners the entry would already be expired when the test asserted `cache.get("c_new") == {"v": "new"}`, producing an `AssertionError: assert None == {'v': 'new'}`. Raised the TTL to 60s — still much shorter than the 10,000s of the other rows (which is what the test actually needs to exercise FIFO-vs-expires_at ordering), and now comfortably above any realistic put→get latency.
- **README: surface GitHub and feedback links in setup**: Added an active-development callout to Quick Start that points to the [feedback page](https://luthien.cc/feedback/) and the GitHub repo, and linked the feedback page from the active-development note.
- **Release tooling accepts breaking changes**: the changelog compiler now has a `Breaking Changes` category, and auto-tag bumps the major version when one is pending (it previously always bumped the patch). Fixed a fragment that was missing its frontmatter and had blocked every auto-tag run since May 29.
- **Remove dead `storage` module**: Delete `src/luthien_proxy/storage/` and its tests. The module's only export, `reconstruct_full_response_from_chunks`, operated on OpenAI `chat.completions` chunk shape and had no non-test callers after the Anthropic-only gateway conversion (#351). (#599)
- **Remove deprecated UI redirect routes**: Dropped legacy backwards-compat redirects with no internal callers.
  - `GET /activity/monitor` (previously redirected to `/history`)
  - `GET /debug/diff` (previously redirected to `/diffs`)
  - `GET /history/session/{session_id}` (previously redirected to `/conversation/live/{session_id}`) (#597)
- **Remove `AUTH_MODE=proxy_key` legacy tolerance**: The rename landed in #535 with tolerance code tagged `TODO(post-v0.2): remove`. v0.2 is not shipping, so the tolerance is gone now: `parse_auth_mode()` and its aliases dict, the `_coerce_legacy_auth_mode` Settings validator, the `_read_env_file_value` helper, the leftover-`PROXY_API_KEY` warning, and the `--auth-mode proxy_key` CLI pre-coercion. Adds migration 014 as defense-in-depth: sets `auth_config.auth_mode` default to `both` (Postgres `ALTER`, SQLite table-swap) so operator-authored raw SQL or future code paths that INSERT without explicit `auth_mode` can't resurrect the invalid `proxy_key` value and crash-loop the gateway.
- **Rewrite ARCHITECTURE.md to match the current codebase**: The doc referenced several phantom modules (`llm/litellm_client.py`, `pipeline/processor.py`, `policy_core/openai_interface.py`, `policy_core/streaming_policy_context.py`, a `streaming/` package) and omitted modules that actually exist (`credential_manager`, `policy_manager`, `usage_telemetry/`, `request_log/`, `history/` as a top-level module). The UI route list and data model also drifted. Full rewrite verified against source, migrations, and route decorators.
- **Policy cache round-trip test coverage**: Expanded `PolicyCache` unit tests to cover non-trivial round-trip values — deeply nested dicts/lists, BMP and supplementary-plane unicode (including emoji, ZWJ sequences, combining characters), large payloads (~100KB, including multi-byte), JSON control characters, scalar top-level values, type-preservation for bool vs int, empty containers, None, integer and float edge values, type-changing overwrites, unicode dict keys and cache keys, and policy-name isolation with unicode namespaces. Follow-up to PR #521 review item #11.
- **AGENTS.md / CLAUDE.md parity**: Renamed all `CLAUDE.md` files to `AGENTS.md` and replaced each `CLAUDE.md` with a symlink pointing at its sibling `AGENTS.md`. A CI check (`.github/workflows/agents-parity.yml`) and pre-commit hook enforce that every `AGENTS.md` has a matching `CLAUDE.md` symlink so both names always resolve to the same content.
- **Update CLAUDE.md project structure**: Rewrote the `src/luthien_proxy/` module map to match the actual layout — removed stale `orchestration/` and `streaming/` entries (replaced by `pipeline/`), added missing subpackages (`pipeline/`, `request_log/`, `history/`, `usage_telemetry/`, `credentials/`, `static/`) and key top-level modules (`auth.py`, `session.py`, `credential_manager.py`, `policy_composition.py`, `policy_manager.py`, `gateway_routes.py`, `dependencies.py`, `main.py`, `config.py`, `telemetry.py`, `config_fields.py`, `config_registry.py`). Also promoted `POLICY_SOURCE` and `POLICY_CONFIG` into their own Policy env vars sub-bullet in the Environment Setup section. (#523)

## 3.0.0 | 2026-04-09

### Features

- **Remove OpenAI gateway and Codex support**: The proxy now exclusively supports the Anthropic `/v1/messages` endpoint. Removed `/v1/chat/completions`, LiteLLM request routing, and Codex CLI support. LiteLLM is retained only for policy-internal judge LLM calls. (#351)
- **Diff viewer auto-loads recent calls**: The `/diffs` page now automatically shows the recent calls list on load, instead of requiring a manual "Browse Recent" click.
- **Auto-release for luthien-proxy**: on merge to main, automatically compile changelog fragments, cut a versioned section in CHANGELOG.md, tag the release, and trigger GitHub Release + Docker image publishing. Starts at v3.0.0, auto-increments patch. Also fixes Docker images reporting `0.0.0+sha` instead of the actual version when built from a tag.
- **Chain-first policy config UX**: Overhaul policy configuration page with a unified chain-building experience. Click policies to preview details, press + to add to chain. Visible move/remove controls, blue/green color tinting for proposed/active chains, sticky Proposed and Active columns, proper Alpine.js config forms for chain items, and hidden internal policies by default. (#389)
- **CLI auto-versioning & publishing**: luthien-cli version is now derived from git tags (`cli-v*`) via hatch-vcs. Merging CLI changes to main auto-tags and publishes to PyPI. (#390)
- **`luthien policy` CLI command**: View, list, inspect, and switch gateway policies from the command line with interactive picker support. (#479)
- **Conversation viewer improvements**: Deduplicates cumulative API history, collapses preflight turns (quota probes, title generation), renders XML-tagged sections as collapsible blocks, and pairs tool calls with their results. Backend now extracts Anthropic-style tool_result content blocks with error state.
- **Credential management standardization**: Introduce `Credential` value object and `AuthProvider` config system for typed credential handling across gateway, policies, and judge calls. Add server credential store with optional encryption, admin API for credential CRUD, and `auth_provider` config field for judge policies.
- **`luthien hackathon` command**: One-command hackathon onboarding — forks/clones repo, installs deps, starts gateway from source, interactive policy picker, and prints comprehensive getting-started guide with cheatsheet, UI tour, key files, and project ideas.
  - New `HackathonOnboardingPolicy`: first-turn welcome with hackathon context
  - New `hackathon_policy_template.py`: SimplePolicy skeleton for participants to customize (#397)
- **Docker-free local mode**: `luthien onboard` now defaults to local mode — SQLite database, in-process event publisher, no Docker required. Use `--docker` for the previous PostgreSQL + Redis setup. (#370)
- **Onboarding policy**: New `OnboardingPolicy` appends a welcome message with config links to the first response in a conversation, then becomes inert on subsequent turns.
  - `luthien onboard` now uses the onboarding policy by default (no more policy selection step)
  - `luthien onboard` pre-seeds the onboarding prompt and opens the config page after gateway setup
- **`--proxy-ref` CLI option**: Run `onboard`, `up`, and `hackathon` against a specific branch, commit, or PR (`--proxy-ref '#123'`) instead of defaulting to main (#407)
- **Streaming protocol compliance validator**: Added a pipeline-level validator that checks Anthropic streaming event ordering after each stream completes. Logs warnings on violations (content blocks after message_delta, unclosed blocks, etc.) and records them as policy events and OTel span attributes. This is the architectural prevention for the class of streaming ordering bugs seen in PRs #134 and #356.
- **Synthesized policy configuration UI**: Three-column Available|Proposed|Active layout with PBC-aligned nav
  - Simple/Advanced policy grouping with inline In/Out examples
  - Pydantic/Alpine.js schema-driven config forms
  - Credential source dropdown and dual test panels
  - Single/Chain mode toggle, settings popover in nav
  - Redesigned landing page with progressive disclosure
  - PBC design tokens across all pages (Inter font, frosted glass nav)
  - Supersedes: #372, #376 (#379)
- **Global telemetry dashboard**: Added a Cloudflare Worker at `telemetry.luthien.cc` that relays anonymous usage metrics to Grafana Cloud for worldwide adoption tracking. Updated default telemetry endpoint from `telemetry.luthien.io` to `telemetry.luthien.cc`. (#460)
- **History page clarity**: The `/history` page is now titled "Sessions" with the subtitle "Each session is one conversation — click to view". Session cards show a `→` arrow that highlights green on hover, making clickability explicit. Sessions without a message preview are dimmed and italicised instead of showing "No preview available".
- **Version display**: Show proxy version (git commit hash) in CLI onboard output and as a shared footer on all web UI pages. Replaces hardcoded version strings with real build identifiers across all deployment modes. (#507)
- **Conversation viewer rewrite**: SSE-powered live updates (replacing polling), per-turn event timeline with raw JSON, and JSONL export
  - New `ConversationLinkPolicy` injects viewer URL into first response per session
  - JSONL export endpoint at `GET /api/history/sessions/{id}/export/jsonl` (#478)

- **In-process Redis replacement** — activity monitor, credential cache, and all Redis features work without Redis in local single-process mode via `EventPublisherProtocol` and `CredentialCacheProtocol` abstractions
- **Silence OTel errors** (silence-otel): Gracefully handle missing OTel/Tempo infrastructure
  - Default `OTEL_ENABLED` to `false` (opt-in instead of opt-out)
  - Silence gRPC and OTel exporter loggers that spam ERROR on connection failure
  - Docker Compose explicitly enables OTel when running the full stack
  - Log "OTel disabled" at DEBUG instead of INFO
- **CLI progress indicators** (cli-progress): Add spinners to long-running CLI operations so users know the tool isn't hung
  - `luthien onboard`: spinners during image pull, container stop/start, and health check
  - `luthien up` / `luthien down`: spinners during container start/stop and health check
  - `repo.py`: spinners during artifact download and update checks
- **Policy context injection** — injects a system message informing the LLM about active policies, preventing model confusion when policies modify output; configurable via `INJECT_POLICY_CONTEXT` env var (#355)
- **SQLite support** for Docker-free installs (#344)
- **`luthien onboard`** interactive setup command — prompts for policy description, generates keys, starts stack (#317)
- **Auto-fetch proxy artifacts on onboard** — `luthien onboard` downloads Docker artifacts from GitHub, no repo checkout needed (#345)
- **Mock e2e testing framework** — real HTTP requests against a fake Anthropic backend, no API calls or cost (#307)
- **Surface upstream billing mode** to prevent unexpected API charges (#311)

### Fixes

- **Batch env var validation**: Report all missing required environment variables at once instead of failing on the first one. (#416)
- **Auto-start gateway from `luthien claude`**: `luthien claude` now automatically starts the gateway if it isn't running, instead of letting Claude Code fail with ConnectionRefused. (#403)
- **Docker entrypoint SQLite fix**: Skip Postgres migrations when DATABASE_URL is a SQLite URL, and filter `sqlite_schema.sql` from the Postgres migration glob. (#415)
- **Docker local build fallback**: When `docker compose pull` fails (e.g. GHCR 403), onboarding now offers to clone the repo and build images locally instead of exiting. (#455)
- **Docker onboard error messaging**: Narrow GHCR auth-failure detection from bare "denied" to "access denied" to avoid false positives on Docker socket permission errors, and strengthen test coverage for `_download_files` error paths. (#454)
- **Fix hackathon command crash**: Remove `import yaml` re-introduced by the hackathon command after pyyaml was dropped as a dependency. (#402)
- **Sanitize client-facing error detail leakage**: Replace raw `str(e)` exception messages in HTTP responses with generic messages across pipeline, admin, and history routes. Internal details (Pydantic traces, DB errors, module paths) are now logged server-side with `repr(e)` only and never forwarded to clients. Set `VERBOSE_CLIENT_ERRORS=true` to restore verbose error details for local debugging. (#313)
- **Persist ADMIN_API_KEY to .env during local onboard**: `luthien onboard` now writes `ADMIN_API_KEY` to the gateway `.env` file, preventing auth failures after gateway restart caused by the gateway generating a new random key on each startup.
- **Onboarding QA fixes**: Multiple fixes from first-round QA testing
  - Install script now checks for working `git` before proceeding (macOS Xcode CLI tools)
  - Fixed wrong Claude Code package name in error message (`claude-cli` → `claude-code`)
  - Added transparent `/v1/*` API passthrough so Claude Code endpoints beyond `/v1/messages` don't 404
  - Fixed Sentry crash on invalid DSN by validating URL format before `sentry_sdk.init()`
  - Fixed gateway port instability by stopping old gateway before selecting a new port
  - Fixed Claude Code TUI freeze when launched via `curl | bash` by reopening stdin from the real pty device
  - Fixed 500 errors on non-streaming requests for Opus models by using streaming internally in `complete()` (#490)
- **Fix CLI install and CI publish pipeline**: CI workflows (`auto-tag-cli`, `release-cli`) were failing because `uv run pytest` didn't install dev extras. Install scripts now pull from GitHub source instead of stale PyPI package. (#404)
- **ConversationLinkPolicy**: Link now appears in the actual conversation response instead of being silently consumed by Claude Code's invisible preflight call
- **Policy diff viewer**: Fix "str object has no attribute isoformat" error when loading recent calls on SQLite
- **Fix broken e2e tests and add single-command test runner**: Replace fragile module-level monkey-patching with pytest fixture overrides for e2e test config (gateway_url, api_key, auth_headers). Fixes 40 sqlite_e2e tests broken since PR #410, plus 5 mock_e2e test failures from hardcoded ports, stale Docker fallbacks, and import-time settings caching. Adds `scripts/run_e2e.sh` to orchestrate all e2e tiers (sqlite, mock, real) with automatic setup/teardown — no Docker needed for sqlite or mock tiers. All 210 tests (42 sqlite + 168 mock) now pass from a single `./scripts/run_e2e.sh sqlite mock` invocation. (#494)
- **launch_claude_code.sh starts wrong service**: The script called `observability.sh up -d` instead of `start_gateway.sh` when the gateway health check failed, so the gateway never actually started. (#452)
- **OnboardingPolicy in MultiSerialPolicy chains**: Fixed two bugs preventing proper composition.
  - OnboardingPolicy hook methods silently failed because `context.request` is always `None` in the Anthropic path. Now stashes the request via `get_request_state()`.
  - `MultiSerialPolicy.on_anthropic_stream_complete` now chains each policy's emissions through remaining policies' `on_anthropic_stream_event`, so downstream transforms (e.g. AllCapsPolicy) apply to all content including welcome messages. (#409)
- **Sanitize Redis URL in logs**: Strip credentials from Redis connection URL before logging to prevent credential exposure. (#482)
- **Prevent Sentry initialization during test runs**: litellm's `load_dotenv()` was picking up `SENTRY_ENABLED=true` from the repo's `.env` before the test guard could run, causing test exceptions to be sent to production Sentry. Moved the guard to module-level in `tests/conftest.py` with force-set instead of `setdefault`. (#486)
- **Anthropic prompt cache tokens now forwarded**: `cache_creation_input_tokens` and `cache_read_input_tokens` are included in the response usage object when present, both for non-streaming and streaming responses. Previously these were silently dropped, preventing users from tracking prompt caching effectiveness.
- **Reduce gateway memory footprint to fit 1G Docker limit**: Skip duplicate raw-event buffering during streaming, make client cache size configurable via `ANTHROPIC_CLIENT_CACHE_SIZE`, and validate `LOG_LEVEL` at startup. (#453)
- **Fix local onboarding and Docker port conflicts**: Install `luthien-proxy` from GitHub instead of PyPI (not yet published). `luthien up` in Docker mode now auto-selects free ports for conflicting services and saves the resolved gateway URL to config so `luthien claude` routes correctly. (#385)
- **Onboarding discoverability improvements**: Add "next step" nudge in README after setup, detect Docker Compose v1 with a specific upgrade message in quick_start.sh (#456)
- **Fix onboarding crashes**: Bundle `sqlite_schema.sql` with the Python package so the gateway can create database tables in pip-installed environments. Remove `pyyaml` dependency from CLI by writing config YAML directly.
  - Gateway no longer crashes with "no such table: current_policy" on fresh `luthien onboard`
  - `_write_policy` no longer fails with `ModuleNotFoundError: No module named 'yaml'`
  - README now explains dashboard API key requirements and localhost auth bypass (#399)
- **Optional ADMIN_API_KEY**: Gateway no longer crashes on startup when `ADMIN_API_KEY` is unset — admin endpoints handle the missing key gracefully at request time instead. (#405)
- **PROXY_API_KEY no longer required**: The proxy no longer requires `PROXY_API_KEY` to be set — `AUTH_MODE=both` degrades gracefully to passthrough-only. Neither `luthien onboard` nor `luthien hackathon` generate a proxy key. `AUTH_MODE=proxy_key` without a key is now a hard startup error. (#476)
- **Reject missing max_tokens**: Requests without `max_tokens` now return 400 instead of silently defaulting to 4096. Removed hallucinated `max_output_tokens` alias. Matches real Anthropic API behavior.
- **Fix session ID not recorded in OAuth passthrough mode**: Fall back to `x-session-id` header when `metadata.user_id` is absent or doesn't match the API key session format. Conversation history is now recorded for OAuth users. (#386)
- **Inject error message when judge failure silently strips all content**: When `on_error: block` is configured and the safety judge fails (auth error, network, rate limit), all content blocks were previously dropped with no explanation — the gateway returned an empty response with `stop_reason: end_turn` and Claude Code showed "Cogitated for Xs" then nothing. The gateway now injects an error text block in both the non-streaming and streaming paths explaining that the response was blocked due to a judge failure. (#451)
- **Single-pass dev_checks.sh**: Removed the pre-clean-tree check and auto-stages formatting fixes instead of failing. No more two-pass commit dance.
  - Script paths now use `git rev-parse --show-toplevel` for worktree compatibility (#422)
- **Streaming pipeline leaked Python SDK synthetic events to wire-protocol clients**: The Anthropic Python SDK's high-level `MessageStream` injects synthetic helper events (`text`, `thinking`, `citation`, `signature`, `input_json`) that have no wire-protocol counterpart. These were forwarded to clients, breaking strict validators like `@ai-sdk/anthropic`. Fixed by switching from `messages.stream()` (high-level `MessageStream` with synthetic events) to `messages.create(stream=True)` (raw `AsyncStream[RawMessageStreamEvent]` yielding only wire-protocol events). This eliminates the problem structurally — no blocklist/allowlist maintenance required. (#499)
- **Fix streaming protocol violation in SimpleLLMPolicy**: When tool_use blocks are blocked by the judge, emit an explanatory text block (e.g., `[Tool call `Bash` was blocked by policy]`) instead of an orphaned `content_block_stop` or empty response. This fixes the Anthropic streaming protocol violation and allows Claude Code to continue the conversation after a tool call is blocked. (#443)

- Fix worktree dev instances sharing `COMPOSE_PROJECT_NAME` — auto-derive from directory name (#348)
- Make gateway API key optional for `luthien claude` — OAuth passthrough by default (#346)
- Fix onboard port conflicts and add API key warning (#341)
- Return Anthropic-format errors for `/v1/messages` HTTPExceptions (#315)
- Explicit backend timeout (`ANTHROPIC_BACKEND_TIMEOUT_SECONDS = 600`) and safe policy error handling (#310)
- Stop setting `ANTHROPIC_API_KEY` in launch scripts — let Claude Code use its own credentials (#318)
- Inject warning on SimpleLLMPolicy judge failure instead of silent pass-through (#329)
- Fix silent policy class errors in `_load_from_db()` — no longer silently downgrades to YAML config (#327)
- Narrow broad except in file-fallback-db policy init to `FileNotFoundError` (#328)
- Narrow bare except to `ValueError` in admin JSON parsing (#326)
- Narrow DB exception handling and add drop counters (#330)
- Add logging to 12 silent exception handlers across codebase (#338)
- Fix test-chat Docker selfcall, nullable param example, pair input UI (#306)
- Show Deactivate button instead of Reactivate for active policy on `/policy-config` (#333)
- Clear nav billing badge polling interval on Alpine destroy (#323)
- Fix mock e2e tests on Linux with correct Docker networking and auth (#331)
- Fix `quick_start.sh` health check reliability (#289)
- Add `--build` to `quick_start.sh` to prevent stale Docker images (#299)
- Sanitize sensitive headers in DebugLoggingPolicy (#301)
- Fix MultiParallelPolicy deepcopy crash and MultiSerialPolicy response ordering (#305)
- Fix overseer test harness usability improvements (#294)
- Fix hardcoded database name in migration 008 (#281, #282)
- Add dirty-tree warning to `dev_checks.sh` (#324)

### Refactors

- **Billing badge accessibility & polish**: Remove duplicate tab stop on badge, skip tooltip repositioning when hidden, replace Unicode escapes with literal characters, and add spatial-separation comment for the body-appended tooltip. (#477)
- **Consolidate conversation views**: Merged `/activity/monitor`, `/history/session/{id}`, and `/conversation/live/{id}` into a single live conversation viewer at `/conversation/live/{id}`
  - Session list at `/history` now links directly to the live view
  - Live view renders turns incrementally (new turns slide in without re-rendering existing ones)
  - Raw event stream viewer preserved at `/debug/activity` for low-level debugging
  - Old URLs (`/activity/monitor`, `/history/session/{id}`) 301-redirect to their replacements (#501)
- **Unify policy interface to hooks-only**: Replace dual `run_anthropic`/hooks execution model with hooks as the sole interface. Eliminates ~660 lines of duplicate logic and the bug class from PR #409.
  - Remove unused `MultiParallelPolicy`
  - `AnthropicExecutionInterface` protocol now defines 4 hook methods instead of `run_anthropic`
  - Executor owns backend I/O; policies only implement hooks (#421)
- **Dual SQLite/Postgres migration sync**: Replace the hand-maintained SQLite schema snapshot with incremental per-dialect migration files. SQLite migrations now run incrementally (matching Postgres behavior), with automatic bootstrap for existing databases. A CI schema comparison test catches drift between the two dialects. (#442)
- **Remove dead match entry in parse_judge_response**: Remove unreachable `"```json"` from the prefix match set — `lstrip("`")` already strips all backticks before the check. (#483)
- **Switch pytest to --import-mode=importlib**: Eliminate test filename collision workarounds and sys.path hacks by using fully-qualified module paths for test imports. Shared constants moved to `tests/constants.py`.
- **Remove prefix-based credential type heuristic**: The gateway now relies solely on the transport header (`Authorization: Bearer` vs `x-api-key`) to determine credential type. Removed `is_anthropic_api_key()` and the `oauth_via_api_key` credential type that second-guessed the transport header using token prefix inspection.
- **Remove dead code from ToolCallJudgePolicy**: Delete unused `_call_judge_with_failsafe()` and `_create_judge_failure_message()` methods left over from a previous refactor. (#445)
- **Reorganize tests by package**: Move tests into `tests/luthien_proxy/` and `tests/luthien_cli/` subdirectories. Add `luthien-cli` as an editable dev dependency so CLI tests run from the root venv. (#410)
- **Type strictness pass**: Replace `Any` with concrete types (`AnthropicContentBlock`, `ToolCallDict`, `JSONObject`, `AnthropicRequest`) across 12 files; remove 208 lines of dead code (`transaction_recorder.py`). Also fixes a latent `AttributeError` in `PolicyContext.__deepcopy__` where `.model_copy()` was called on a non-Pydantic field. (#461)

- Move luthien-cli into `src/` for consistent project layout (#321)
- Encapsulate credential type tracking in CredentialManager (#325)

### Chores & Docs

- **CLI install: pipx → uv tool**: All references to `pipx` replaced with `uv tool install`. Install scripts now auto-detect and migrate existing pipx installations.
- **COE follow-up for PR #356**: Added streaming event ordering invariant to `dev/context/gotchas.md` — all content blocks must precede `message_delta` in Anthropic streaming protocol
- **Dockerless default**: Documentation and `.env.example` now default to dockerless mode (SQLite, no Postgres/Redis) for development and single-user local use. Docker Compose with Postgres+Redis is positioned for multi-user production deployments. `quick_start.sh` now validates that DATABASE_URL isn't SQLite before starting Docker services. (#391)
- **OWASP threat scenario e2e tests**: 48 new mock_e2e tests covering LLM01 (Prompt Injection), LLM06 (Sensitive Disclosure), LLM08 (Excessive Agency), gateway robustness, and audit trail (8+8+16+11+5 across 5 files).
  - OWASP LLM markers (llm01/02/04/06/07/08) added to pytest config for selective test runs
  - Fixed 4 pre-existing test failures: passthrough auth tests now use `MOCK_ANTHROPIC_HOST` env var (defaults to `host.docker.internal`, overridable to `localhost` for dockerless/CI runs); `_enable_request_logging` fixture is a no-op when `ENABLE_REQUEST_LOGGING` is already set in the environment (#458)
- **Move dev tools to dev dependencies**: Moved `pyright`, `vulture`, and `pytest-timeout` from production `[project.dependencies]` to the `[dependency-groups] dev` group so they aren't pulled in by `pip install luthien-proxy`. (#444)
- **OAuth passthrough docs**: Restructured README Configuration section to lead with OAuth passthrough as the default auth mode. Added prominent billing warning for API key mode. (#468)
- **Policy reference guide**: Add comprehensive `docs/policies.md` with full examples for all policies, presets, composition patterns, admin API usage, and custom policy authoring guide. Update README available policies section.
- **Docs & UI polish**: Switched judge model examples from gpt-4o-mini to Haiku, clarified probability_threshold comment, explained YAML `class:` field, renamed "Dogfood mode" to "Safety mode" on policy-config page (#491)
- **README preset policies**: Added all 7 built-in preset policies to the README's available policies section, organized into "Built-in Presets" and "Core Policies" subsections
- **Remove OpenAI/GPT references**: Cleaned up config files, deploy docs, startup scripts, and .env.example to remove OPENAI_API_KEY references and replace GPT model examples with Claude/Anthropic equivalents. Functional code (OpenAI format pipeline, GPT model detection in judge utils) left intact. (#503)
- **Synchronize local and CI dev checks**: Pin pyright version, enforce locked dependency sync, require shellcheck, and fail on uncommitted formatting changes — eliminating common sources of local/CI divergence.
- **ToolCallJudgePolicy unit tests**: Add comprehensive unit tests for ToolCallJudgePolicy covering streaming tool pass/block, non-streaming responses, state cleanup, and blocked message formatting. Extract shared Anthropic streaming event builders into reusable test helper module. (#448)
- **Trivial local startup from source**: `scripts/start_gateway.sh` now auto-creates `.env` from `.env.local.example` when no `.env` exists, eliminating the manual copy step for first-time dev setup. README `## Development` section now includes a "Quick Start (from source, no Docker)" subsection with the full clone-to-running sequence. (#471)
- **Add uninstall instructions to README**: New `## Uninstall` section covers stopping the gateway, removing the CLI (`uv tool uninstall luthien-cli`), and cleaning up the data directory (`~/.luthien`), for both local and Docker modes. (#473)

- Add shellcheck to `dev_checks.sh` and fix all 22 warnings across 9 scripts (#332)
- Update README to match landing page v10.7 (#319)
- Add ARCHITECTURE.md codebase map (#293)
- Document bash 3.2+ requirement on all shell scripts (#320)
- Bump luthien-cli to 0.1.7 (#342)
- Update claude-code-action to v1 (#283)
- Remove `dev/TODO.md`, track TODOs on Trello (#300)
- Remove dead persistence pipeline (`storage/persistence.py`) (#285)
- Define `DEFAULT_TEST_MODEL` constant (#288)
- Add `.claude/worktrees/` to `.gitignore` (#279)
- Update Railway deployment config (#334)

### Previously logged (pre-#306)

- Remove dead Anthropic compatibility handlers (`_handle_streaming`, `_handle_non_streaming`) from `anthropic_processor.py`
  - Update unit/regression tests to exercise `process_anthropic_request()` instead of deleted internal wrappers
  - Refresh docs/examples to use current policy classes and Anthropic execution runtime terminology
- Remove dead Anthropic streaming executor path
  - Delete unused `src/luthien_proxy/streaming/anthropic_executor.py`
  - Delete executor-only unit tests and migrate requirement regressions to assert streaming behavior through `process_anthropic_request()`
  - Tighten Anthropic execution-runtime type guard and add tests for invalid streaming emissions and upstream `io.complete()` failures

- Refactor: extract `restore_context()` context manager for OpenTelemetry span context management
  - Replaces manual `attach`/`detach` pattern in both `anthropic_processor.py` and `processor.py`
  - Guarantees cleanup even on exception, reduces nesting depth, improves readability

- Fix Anthropic observability pipeline: events not written to DB, generic error types, empty conversation history (#249)

- Fix default auth_mode from `proxy_key` to `both` so Claude Code OAuth works on fresh setups (#222)
  - DB migration: `008_default_auth_mode_both.sql`
  - Also updates existing `proxy_key` rows to `both`

- Add general-purpose policy composition API (policy-composition)
  - `compose_policy()` function for inserting policies into chains at runtime
  - `MultiSerialPolicy.from_instances()` for building chains from pre-instantiated policies
  - `DogfoodSafetyPolicy` — regex-based safety policy that blocks dangerous commands
    (docker down, pkill, rm .env, DROP TABLE) when proxying through the gateway
  - `DOGFOOD_MODE` env var to auto-inject DogfoodSafetyPolicy into any policy chain
  - Replaces hacky approach from #243 with clean, reusable composition mechanism

- Fix SamplePydanticPolicy crash on activation (#250)
- Add MultiSerialPolicy and MultiParallelPolicy for composing control policies (#184)
  - MultiSerialPolicy: sequential pipeline where each policy's output feeds the next
  - MultiParallelPolicy: parallel execution with configurable consolidation strategies
    (first_block, most_restrictive, unanimous_pass, majority_pass, designated)
  - Both support OpenAI and Anthropic interfaces with interface compatibility validation
  - Shared `load_sub_policy` utility for recursive policy loading from YAML config
- Add configurable passthrough authentication (passthrough-auth)
  - Three auth modes: `proxy_key`, `passthrough`, `both` (default) - configurable at runtime via admin API
  - Credential validation via Anthropic's free `count_tokens` endpoint with Redis caching
  - Configurable TTLs for valid (1hr default) and invalid (5min default) credential cache
  - Admin API: `GET/POST /admin/auth/config`, `GET/DELETE /admin/auth/credentials`
  - Admin UI: `/credentials` page for managing auth modes and viewing cached credentials
  - Supports OAuth token passthrough for Claude Code
  - `x-anthropic-api-key` header still supported for explicit client key override
  - DB migration: `007_add_auth_config_table.sql`

- Add `/client-setup` endpoint with setup guide for connecting Claude Code to the proxy (deploy-instructions)

- Add conversation live view with diff display (#186)
  - New `/conversation/live/{id}` endpoint for real-time conversation monitoring with diff visualization
  - "Live View" link from history detail page
  - E2E tests for conversation live view

- Fix login redirect to send user back to original page after auth (#195)
  - Hidden form field was named `next` but POST handler expected `next_url`
  - Integration tests for redirect behavior

- Add multi-turn e2e test with /compact for Claude Code sessions (#182)
  - `test_claude_code_multiturn_with_compact` exercises full multi-turn session lifecycle through the proxy
  - `run_claude_code()` now supports `resume_session_id` parameter for `--resume`

- Support multiple dev docker deployments on the same machine (#183)
  - Parameterize all hardcoded ports in `docker-compose.yaml` via env vars with sensible defaults
  - `COMPOSE_PROJECT_NAME` in `.env.example` isolates networks, volumes, and container names per deployment

- Add Apache 2.0 LICENSE file (#181)

- Move internal planning docs to luthien-org (#173)
  - Remove 38 historical planning files from `dev/archive/` and 4 outdated v1 docs from `docs/archive/`
  - Reduces noise for contributors in the public repo

- Document web UI consolidation strategy with endpoint inventory (#189)

- Fix E2E test failures: docker env override and metadata validation (#172)
  - Fix shell env vars overriding `.env` file API keys
  - Update tests for Anthropic API metadata validation changes

- Fix SimplePolicy non-streaming support (#147, #168)
  - SimplePolicy-based policies previously only worked for streaming responses
  - Add `on_response()` hook so policies work when `stream: false`

- Forward backend API errors to clients with proper format (#146)
  - Backend LLM errors (auth failure, rate limit, invalid request) now return properly formatted responses matching the client's API format
  - Previously caused generic 500 errors that made clients like Claude Code hang

- Fix compatibility issues caused by litellm update (#143)

- Refactor: stricter typing in history service (#139)
  - Add `event_types.py` with TypedDicts for structured event data
  - Discriminated unions for content blocks (text, tool_use, tool_result, image)
  - Replace `dict[str, Any]` with proper typed dicts

- Refactor: use dedicated `thinking_blocks` field instead of overloading content (#138)
  - Add `ThinkingBlock` and `RedactedThinkingBlock` TypedDict types
  - Revert `content` back to `str | None` with separate `thinking_blocks` field

- Improve gateway homepage (#132)
  - Add missing UI links (`/policy-config`, `/history`)
  - Add "Auth Required" badges to protected endpoints
  - Add Quick Start shortcuts for common tasks

- Fix docker-compose project name collision across worktrees (fix/docker-project-names)
  - Derive `COMPOSE_PROJECT_NAME` from worktree directory name (e.g. `luthien-main`, `luthien-deploy-instructions`)
  - Add `name:` field to `docker-compose.yaml` with `luthien` default for raw `docker compose up`
  - Comment out `COMPOSE_PROJECT_NAME` in `.env.example` so new setups get auto-derivation

- Remove Grafana, Loki, and Promtail from observability stack (remove-loki-grafana)
  - Keep Tempo for distributed tracing and OpenTelemetry instrumentation
  - Remove `observability/grafana/`, `observability/grafana-dashboards/`, `observability/loki/`, `observability/promtail/` directories
  - Remove Grafana/Loki/Promtail services from docker-compose.yaml
  - Remove `GRAFANA_URL` setting from `.env.example` and `Settings` class
  - Update `build_tempo_url()` to generate direct Tempo API URLs instead of Grafana Explore URLs
  - Update `scripts/observability.sh` for Tempo-only stack
  - Remove `scripts/test_observability.sh` (was Loki-dependent)
  - Update all documentation references

- Add SaaS infrastructure provisioning CLI for Railway (saas-infra)
  - New `saas_infra/` package with CLI for managing multi-tenant proxy instances
  - Commands: create, list, status, delete, redeploy, cancel-delete, whoami
  - Each instance gets isolated Railway project with Postgres + Redis + gateway
  - Soft delete with 7-day grace period before permanent deletion
  - Railway GraphQL API integration via httpx
  - JSON output mode for scripting (`--json` flag)
  - See `saas_infra/README.md` for usage documentation

- Fix E2E test failures and multi-event streaming support (#174)
  - `on_anthropic_stream_event` returns `list[AnthropicStreamEvent]` instead of single event
  - Policies can now emit multiple events per input (e.g. `[delta, stop]`)
  - SimplePolicy returns both events directly, removing `get_pending_stop_event` hack
  - ToolCallJudgePolicy streaming now works: blocked calls emit replacement text, allowed calls re-emit buffered events
  - Fix Claude Code E2E auth (`ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY`)
  - Remove unsupported cross-format routing tests (Phase 2)
  - All 9 previously-failing E2E tests resolved

- Remove local Ollama container and all related configuration
  - Deleted docker/Dockerfile.local-llm, docker/local-llm-entrypoint.sh
  - Deleted config/local_llm_config.yaml, config/archive/demo_judge.yaml
  - Removed local-llm service and local_llm_models volume from docker-compose.yaml
  - Updated documentation to remove Ollama references

- Refactor policies to use platform-specific interfaces (split-apis)
  - Add `BasePolicy`, `OpenAIPolicyInterface`, `AnthropicPolicyInterface` ABCs
  - Unified policies implement both OpenAI and Anthropic interfaces
  - Rename hooks to `on_openai_*` and `on_anthropic_*` for clarity
  - Processors use `isinstance` checks for interface dispatch
  - Delete `policies/anthropic/` directory - all policies now in main `policies/`
  - Delete deprecated `AnthropicPolicyProtocol`

- Fix StringReplacementPolicy dropping finish_reason causing blank responses in Claude Code
  - Content and finish_reason must be emitted as separate chunks
  - SSE assembler's `convert_chunk_to_event()` returns early on content, ignoring finish_reason
  - Added e2e test to verify complete SSE event structure (message_delta, content_block_stop)

- Reorganize LLM types into separate OpenAI and Anthropic modules (#117)
- Fix thinking blocks stripped from non-streaming responses (#128)

- Pass through extra model parameters like `thinking`, `metadata`, `stop_sequences` (thinking-flags)
  - Anthropic requests now preserve all extra parameters during format conversion
  - Map `stop_sequences` (Anthropic) → `stop` (OpenAI)
  - Convert `tool_choice` format between Anthropic and OpenAI APIs
  - OpenAI requests already preserved extra params via Pydantic `extra="allow"`
  - Enables extended thinking, reasoning effort, and other provider-specific features
  - 14 new e2e tests validate parameter pass-through for both client types

- Auto-discovering policy configuration UI (policy-config-ui)
  - `/admin/policy/list` now auto-discovers all policies from `luthien_proxy.policies`
  - Config schemas extracted from constructor signatures using type hints
  - Policy config UI (`/policy-config`) generates form fields based on schema
  - Simple types get appropriate inputs (text, number, checkbox)
  - Complex nested types (dict, list) get JSON textarea
  - Fixes broken create/activate endpoints that didn't exist

- Add Railway demo deployment configuration (`railway.toml`, `deploy/README.md`)

- Add conversation history viewer with styled message types and markdown export (conversation-history-viewer)
  - Browse recent sessions at `/history` with turn counts, policy interventions, and model usage
  - View full conversation detail at `/history/session/{id}` with message type styling (system/user/assistant/tool call/tool result)
  - Policy annotations shown inline on turns that had interventions
  - Export any session to markdown via `/history/api/sessions/{id}/export`

- Improve conversation history list UI (#133)
  - Add first user message preview for at-a-glance session recognition
  - Add quick filters: Today, This week, Last week, Last 30 days, Claude Code, Codex
  - Add "More filters" dropdown with sort options (newest, oldest, longest, shortest) and policy activity filters
  - Sticky search/filter bar with magnifying glass icon
  - Date grouping (Today, Yesterday, day names, full dates)
  - Consistent green (#4ade80) color scheme matching other Luthien pages

- Increase unit test coverage from 84% to 90% (#115)
- Fix validation error when images in Anthropic requests (#103, #104)
- Migration validation and fail-fast checks (#110)
  - `run-migrations.sh` validates DB state against local files before applying
  - Gateway startup check ensures all migrations are applied
  - Fails fast with clear errors if: migrations missing locally, unapplied migrations, or hash mismatch
  - Records content_hash for each migration to detect modifications

- Improve login page UX (dogfooding-login-ui-quick-fixes)
  - Add show/hide password toggle below input field (avoids conflict with password managers)
  - Add clickable dev key hint for development environments
  - Add guidance for production users to check .env or contact admin
- Structured span hierarchy for request processing (luthien-proxy-a0r)
  - All pipeline phases (process_request, policy_on_request, send_upstream, process_response) are now visible as siblings in Grafana/Tempo
  - Add `luthien.policy.name` attribute to root span for easy policy identification
  - Add `request_summary` and `response_summary` fields to PolicyContext for policy-defined observability

- Dependency injection for `create_app()` (#105)

- Session ID tracking for conversation context (#102)
  - Extract session ID from Anthropic `metadata.user_id` (Claude Code format: `user_<hash>_account__session_<uuid>`)
  - Extract session ID from `x-session-id` header (OpenAI format)
  - Persist session ID to database for querying conversations by session
  - Add `RawHttpRequest` dataclass to capture original HTTP request data
  - Add OpenTelemetry span attributes for session tracking (`luthien.session_id`)
  - Debug API now returns session_id in call listings and event responses

- Unify OpenAI and Anthropic endpoint processing (#92)
- Fix broken migration script that prevented migrations from running (#fix-migration-script)
- Replace magic numbers with named constants [constants.py](src/luthien_proxy/utils/constants.py)

- Session-based login for browser access to admin/debug UIs (#88)
  - Add `/login` page with session cookie authentication
  - Protected UI pages (`/activity/monitor`, `/diffs`, `/policy-config`) redirect to login when unauthenticated
  - Sign out links on all protected pages
  - Backwards compatible: API endpoints still accept Bearer token and x-api-key

- Confirmed policy config UI backend integration already complete via PR #66 (feature/policy-ui-backend)

- Centralize environment configuration with pydantic-settings (#refactor/env-config-centralize)
  - Add `Settings` class in `src/luthien_proxy/settings.py` for typed configuration
  - Replace scattered `os.getenv()` calls throughout codebase with centralized settings access
  - Support `.env` file loading via pydantic-settings
  - Add `clear_settings_cache()` for test isolation

- Remove unused prisma dependency (#84)
- Added auth to debug endpoints (#86)
- Inject EventEmitter via DI instead of global state (#dependency_injection)
- Added e2e tests that actually invoke claude code running through the proxy

- Codebase cleanup (#81)
  - Remove dead code: `control_plane/` (stale pycache), `streaming_aggregation.py`
  - Standardize on Python module docstrings (removed ABOUTME convention)
  - Organize and deduplicate TODO.md
  - Update CLAUDE.md and codebase_learnings.md to reflect actual module structure

- Implement trace (tempo) + log (loki) observability

- Add `on_streaming_policy_complete()` lifecycle hook for cleanup (#76)
  - New policy hook called in finally block after all streaming policy processing completes
  - Guarantees cleanup runs even if errors occurred during policy processing
  - Implement buffer cleanup in ToolCallJudgePolicy using new hook
  - Simplify `_validate_tool_call_for_judging()` to return just the tool_call dict

- Streaming and Anthropic client fixes (#75)
  - Fix streaming tool calls missing `message_delta` for Anthropic clients
  - Refactor `AnthropicSSEAssembler` to `streaming/client_formatter`
  - Explicitly implement `ClientFormatter` protocol
  - Fix `ChatCompletionMessageToolCall` typing
  - Remove model registration logic

- Fix ToolCallJudgePolicy inheritance to use BasePolicy instead of PolicyProtocol (#62)
  - Resolves gateway startup failure when ToolCallJudgePolicy is configured
  - Override `on_chunk_received()` to prevent duplicate token streaming bug
  - Fix test mock signature to match `call_judge()` parameters
- Dependency injection improvements (#dependency-injection)
  - Add `Dependencies` container class for centralized service management
  - Create FastAPI `Depends()` functions for type-safe dependency access
  - Derive `event_publisher` lazily from `redis_client` (no duplicate storage)
  - Create `LLMClient` once at startup instead of per-request instantiation
  - Replace `getattr(app.state, ...)` pattern with proper DI

- Observability improvements (#observability-refactor)
  - Refactored `LuthienPayloadRecord` → `PipelineRecord` with simplified all-primitive interface
  - Renamed `payload_type` → `pipeline_stage` for better semantics
  - Optimized label structure for efficient querying (only low-cardinality fields as labels, high-cardinality fields are structured metadata)
  - clarified observability functions; simplified implementations
  - Added utility scripts for Loki validation ([query_loki_fields.py](scripts/query_loki_fields.py), [test_line_format.py](scripts/test_line_format.py))

- Policy authoring improvements (#57)
  - Add `BasePolicy` class with default implementations and convenience methods
  - Add convenience properties to `StreamingPolicyContext` (`last_chunk_received`, `push_chunk()`, `transaction_id`, `request`, `scratchpad`)
  - Comprehensive test coverage for policy callbacks and streaming behavior (1100+ new test lines)

- Remove "v2" concept and consolidate architecture (#55)
  - Moved all code from `src/luthien_proxy/v2/*` to `src/luthien_proxy/*`
  - Updated all imports from `luthien_proxy.v2.*` to `luthien_proxy.*`
  - Renamed `V2_POLICY_CONFIG` env var to `POLICY_CONFIG`
  - Renamed `config/v2_config.yaml` to `config/policy_config.yaml`
  - Updated route prefixes: `/v2/debug` → `/debug`, `/v2/activity` → `/activity`
  - Renamed docker service from `v2-gateway` to `gateway`
  - Moved test directories from `tests/**/v2/` to `tests/**/`

- Cleanup and refactoring (#50)
  - introduced `policy_core` for common streaming/policy utilities
    - moved core abstractions (`PolicyProtocol`, `PolicyContext`, `StreamingPolicyContext` to `policy_core`)
  - split `policies/utils.py` into focused modules `chunk_builders.py`, `response_utils.py`, `tool_call_judge_utils.py`
  - dependency analysis script

## 0.0.2 | 2025-11-07

- **Anthropic streaming fixes** (post-#49):
  - Add `AnthropicSSEAssembler` for stateful SSE event generation with proper block indices
  - Fix `ToolCallJudgePolicy` streaming: add `on_content_delta()`, fix chunk creation with proper `Delta` and `StreamingChoices` types
  - Add `DebugLoggingPolicy` for inspecting streaming chunks
  - 8 regression tests to prevent streaming bugs

- Refactor streaming pipeline to explicit queue-based architecture (#49)
  - Simplified `PolicyOrchestrator.process_streaming_response` to clear 2-stage pipeline
  - PolicyExecutor: Block assembly + policy hooks with background timeout enforcement
  - **TimeoutMonitor**: Dedicated class for keepalive-based timeout tracking (100ms check interval)
    - Detects stalled streams when no chunks arrive within configured threshold
    - Raises `PolicyTimeoutError` with timing details for debugging
    - Automatic keepalive reset on each chunk processed
  - ClientFormatter: Model responses to client-specific SSE format (OpenAI/Anthropic)
  - Explicit typed queues (`Queue[ModelResponse]`, `Queue[str]`) define data contracts
  - Dependency injection pattern for policy execution and client formatting
  - Comprehensive unit tests (32 policy executor tests including 8 timeout enforcement tests, 12 formatter tests)
  - Transaction recording infrastructure at pipeline boundaries

- Add `SimpleEventBasedPolicy` for beginner-friendly policy authoring (buffers streaming into complete blocks)
  - Example policies: `SimpleUppercasePolicy`, `SimpleToolFilterPolicy`, `SimpleStringReplacementPolicy`
  - Comprehensive unit and e2e test coverage

### V2 Architecture Migration ([#46](https://github.com/LuthienResearch/luthien-proxy/pull/46))

**Massive cleanup**: Deleted ~9,735 lines of V1 code, tests, and documentation (48% reduction) while building out V2 architecture.

**Major architectural redesign** from separate LiteLLM proxy + control plane to integrated FastAPI + LiteLLM architecture with event-driven policies and comprehensive observability.

#### Core Architecture ([b04d6cd](../../commit/b04d6cd))

- Integrated V2 gateway combining API gateway, control logic, and LLM integration in single process
- `ControlPlaneService` protocol supporting both local and future networked implementations
- `PolicyHandler` abstraction with event-driven interface for user policies
- Bidirectional streaming with policy control over request/response transformation
- Format converters for OpenAI ↔ Anthropic API compatibility
- Support for both streaming and non-streaming responses

#### Event-Driven Policy System

- New `EventDrivenPolicy` DSL with lifecycle hooks:
  - `on_chunk_started`, `on_content_chunk`, `on_tool_call_chunk`, `on_chunk_completed`
  - `on_request_started`, `on_request_completed`
  - `on_response_started`, `on_response_completed`
- `PolicyContext` for per-request state management and event emission
- `StreamingOrchestrator` for managing streaming response pipelines with timeout handling
- Reference implementations:
  - `NoOpPolicy` / `EventBasedNoOpPolicy` - Pass-through for testing
  - `UppercaseNthWordPolicy` - Text transformation demo
  - `ToolCallJudgeV3Policy` - LLM-based tool call security analysis

#### Observability Infrastructure ([8480e06](../../commit/8480e06), [5882493](../../commit/5882493))

- **OpenTelemetry Integration**:
  - Distributed tracing with Grafana Tempo
  - Automatic span creation for all gateway, control plane, and streaming operations
  - Custom `luthien.*` span attributes (call_id, model, stream status, chunk counts, policy decisions)
  - Trace context propagation through entire request pipeline
  - Log correlation via trace_id/span_id injection
  - OTLP gRPC exporter to Tempo

- **Real-Time Monitoring**:
  - Activity stream via Server-Sent Events (SSE) at `/activity/stream`
  - Live activity monitor web UI at `/activity/monitor` with filtering by call_id/model/event_type
  - Redis pub/sub for real-time event distribution
  - Automatic event publishing for gateway, streaming, and policy lifecycle

- **Debug & Analysis Tools**:
  - Debug API at `/debug/`:
    - `/calls` - List recent calls
    - `/calls/{call_id}` - Get call details
    - `/calls/{call_id}/diff` - Compare original vs transformed content
  - Diff viewer UI at `/diffs` with side-by-side JSON comparison
  - Links to Grafana Tempo traces from all UIs

- **Grafana Dashboards**:
  - Live activity dashboard with auto-refresh (control plane logs, V2 API requests, policy activity, errors)
  - Metrics dashboard (request rate by model, p95 latency, latency breakdown, recent traces)
  - Pre-provisioned dashboards auto-loaded on Grafana startup

- **Log Collection**:
  - Grafana Loki for centralized logging
  - Promtail for Docker container log collection
  - 24-hour retention with aggressive compaction
  - Automatic trace ↔ log correlation

#### V1 Cleanup ([slash-and-burn](../../tree/slash-and-burn))

- **Deleted ~18,000 lines of V1 code**:
  - V1 control plane implementation (separate FastAPI service)
  - V1 proxy integration (separate LiteLLM process)
  - Old callback-based streaming system
  - Legacy policy interfaces and event models

- **Removed Docker services**:
  - `litellm-proxy` (port 4000) - replaced by integrated V2 gateway
  - `control-plane` (port 8081) - merged into V2 gateway
  - `dummy-provider` (port 4015) - test fixture no longer needed

- **Archived documentation** (15 files):
  - `dev/archive/`: 7 completed planning documents
  - `docs/archive/`: 4 V1 architecture guides (v1-reading-guide, v1-developer-onboarding, v1-diagrams, v1-ARCHITECTURE)
  - `config/archive/`: 5 V1 config files + policies directory

- **Deleted 16 obsolete scripts**:
  - V1-specific: `build_replay_examples.py`, `dummy_control_plane.py`, `export_replay_logs.sh`
  - Demo artifacts: `demo_*.py`, `run_demo*.sh`
  - One-off spikes: `test_anthropic_streaming.py`, `test_judge_streaming.py`, etc.

- **Removed infrastructure**:
  - `docker/Dockerfile.litellm` - V1 LiteLLM proxy image
  - 8 environment variables (LITELLM_MASTER_KEY, CONTROL_PLANE_URL, LUTHIEN_POLICY_CONFIG, etc.)
  - Replaced `LUTHIEN_POLICY_CONFIG` → `POLICY_CONFIG`

- **Updated documentation**:
  - Migrated policy configuration examples to EventDrivenPolicy DSL
  - Updated port references (8081 → 8000, removed 4000)
  - Fixed service name references (control-plane → gateway)
  - Created `dev/ARCHITECTURE.md` with V2 core principles

#### Testing & Quality

- Comprehensive unit test coverage for policies, control plane, streaming orchestration
- Integration tests for V2 gateway endpoints
- End-to-end tests with real LLM providers (OpenAI, Anthropic, local Ollama)
- Docker-based testing with `./scripts/test_gateway.sh`
- Type safety with Pyright across all V2 modules

#### Developer Experience

- Single-command setup: `./scripts/quick_start.sh`
- Simplified service architecture: gateway, local-llm, db, redis
- Observability stack: `./scripts/observability.sh up -d`
- Live development with hot reload
- Launch scripts for Claude Code and Codex routing through gateway
- Comprehensive documentation:
  - `dev/event_driven_policy_guide.md` - Policy development guide
  - `dev/observability.md` - Observability features
  - `dev/VIEWING_TRACES_GUIDE.md` - Trace analysis walkthrough
  - `dev/OBSERVABILITY_DEMO.md` - Step-by-step demonstration

#### Configuration

- Single config file: `config/policy_config.yaml`
- Policy selection via class path + config dict
- Environment variables consolidated in `.env.example`
- Docker Compose profiles for optional services (observability)

#### Performance & Reliability

- Streaming pipeline with configurable timeouts
- Redis for ephemeral state and pub/sub
- PostgreSQL with Prisma for persistent state
- Graceful error handling with span error recording
- Health checks for all services
- Connection pooling and async I/O throughout

---

## 0.0.1 | 2025-10-10

**Initial V1 implementation** (archived)

- Basic LiteLLM proxy integration with separate control plane
- Callback-based streaming system
- Initial policy engine with tool call judging
- Database persistence with debug logs
- Redis for caching and ephemeral state
- Demo UI for trace visualization
- Hook-based extensibility system
