# Request Processing & Streaming Pipeline Architecture

**Last Updated**: 2026-04-10

This document describes how Anthropic `/v1/messages` requests flow through the gateway in the current code (`src/luthien_proxy/`). The authoritative source is `src/luthien_proxy/pipeline/anthropic_processor.py` — read that file if anything here disagrees.

> **Historical note**: Earlier revisions of this document described a short-lived queue-based pipeline (`PolicyOrchestrator` + `PolicyExecutor` + `ClientFormatter` + `Queue[ModelResponse]`) built around LiteLLM's `ModelResponse` type. Those modules no longer exist. The gateway now talks to Anthropic through the Anthropic SDK directly and runs policies via a hook-based execution interface.

---

## Endpoint Scope

The gateway exposes the Anthropic Messages API at `/v1/messages`. Requests are handled natively, so features like extended thinking, tool use, and prompt caching pass through without format conversion. Non-Anthropic clients are out of scope in this document.

## Request Lifecycle

Every request passes through four phases, wrapped in an OpenTelemetry span hierarchy rooted at `anthropic_transaction_processing`:

```
anthropic_transaction_processing
  +-- process_request
  +-- process_response
  |   +-- policy_execute
  |   +-- send_upstream  (zero or more backend calls)
  +-- send_to_client     (non-streaming only)
```

### 1. Ingest & Authenticate

`gateway_routes.py` validates the credential via `CredentialManager`, resolves the backend `AnthropicClient` (proxy-key vs passthrough vs explicit `x-anthropic-api-key`), and dispatches to `process_anthropic_request()`.

### 2. Build PolicyContext

`process_anthropic_request()` (in `pipeline/anthropic_processor.py`) creates a `PolicyContext` for the call:

- `transaction_id` (a new `call_id`)
- `raw_http_request` snapshot (headers, body)
- `session_id` (extracted from the request body / headers)
- `user_credential` and `credential_manager` for auth-provider resolution
- `emitter` for observability events

If `inject_policy_context` is enabled and the policy derives from `BasePolicy`, a small system-prompt addition listing the active policy names is injected into the request.

### 3. Run the Execution Policy

Policies implement `AnthropicExecutionInterface` (in `policy_core/anthropic_execution_interface.py`) — a hook-based protocol. The processor's `_run_policy_hooks()` function owns the backend call and stream iteration, invoking four hooks in sequence:

- `on_anthropic_request(request, context) -> AnthropicRequest` — transform the request before it is sent to the backend.
- `on_anthropic_response(response, context) -> AnthropicResponse` — transform a non-streaming backend response.
- `on_anthropic_stream_event(event, context) -> list[MessageStreamEvent]` — transform or replace each backend stream event (each call may emit zero or many events).
- `on_anthropic_stream_complete(context) -> list[AnthropicPolicyEmission]` — emit any tail emissions after the backend stream finishes (e.g. injected blocks). `AnthropicPolicyEmission = AnthropicResponse | MessageStreamEvent`, but `_handle_execution_streaming` enforces a single shape per request: on the streaming path it raises `TypeError` if a policy tries to emit an `AnthropicResponse`, and `_handle_execution_non_streaming` symmetrically rejects `MessageStreamEvent`s on the non-streaming path. In practice, `on_anthropic_stream_complete` should only yield `MessageStreamEvent`s.

Policies never see the IO layer directly. The executor wraps the backend in an `_AnthropicPolicyIO` helper (implementing `AnthropicPolicyIOProtocol`) that holds the current request and exposes `io.stream(...)` / `io.complete(...)`. The executor calls `io.stream(request)` or `io.complete(request)` exactly once per request — based on the incoming `stream: true/false` flag — and runs each hook around that call. Each backend call executes under a `send_upstream` child span.

`_execute_anthropic_policy()` dispatches the resulting emissions to either `_handle_execution_non_streaming` (single `AnthropicResponse` → `JSONResponse`) or `_handle_execution_streaming` (sequence of `MessageStreamEvent` → SSE).

### 4. Emit to Client and Record

**Non-streaming** (`_handle_execution_non_streaming`): the processor captures the original backend response (before policy mutation) and the final emitted response, records a `transaction.non_streaming_response_recorded` event, and returns a `JSONResponse`.

**Streaming** (`_handle_execution_streaming`): the processor iterates the policy emissions, serializes each event with `_format_sse_event`, and yields them to the client as `text/event-stream`. In the `finally` block it:

- Validates the accumulated event sequence against `validate_anthropic_event_ordering` (log-and-warn only — violations are recorded as `streaming.protocol_violation` events).
- Reconstructs an `AnthropicResponse` from the accumulated stream events.
- Records `transaction.streaming_response_recorded` with both the raw and final responses.
- Flushes request-log records and records usage telemetry (input/output tokens) when the stream completed normally.

Mid-stream errors after headers have been sent produce an in-stream Anthropic error event (`_build_error_event`) so the client sees a structured failure instead of a silent truncation.

## Key Abstractions

| Abstraction | Location | Role |
|-------------|----------|------|
| `process_anthropic_request` | `pipeline/anthropic_processor.py` | Top-level phase 1–4 orchestration for Anthropic requests |
| `AnthropicExecutionInterface` | `policy_core/anthropic_execution_interface.py` | Hook-based contract for Anthropic policies (`on_anthropic_request`, `on_anthropic_response`, `on_anthropic_stream_event`, `on_anthropic_stream_complete`) |
| `AnthropicPolicyIOProtocol` | `policy_core/anthropic_execution_interface.py` | Executor-only I/O surface (`complete`, `stream`, current request) — policies do not see this directly |
| `AnthropicClient` | `llm/anthropic_client.py` | Async Anthropic SDK wrapper (api_key vs bearer-token auth) |
| `PolicyContext` | `policy_core/policy_context.py` | Per-request state — transaction id, emitter, typed request-state slots |
| `EventEmitterProtocol` | `observability/emitter.py` | Records transaction + policy events to storage and Redis |
| `validate_anthropic_event_ordering` | `pipeline/stream_protocol_validator.py` | Post-stream protocol-ordering check |

## LiteLLM Scope

LiteLLM is **not** on the main `/v1/messages` path. Backend calls are always `AnthropicClient` -> Anthropic SDK. `pipeline/anthropic_processor.py` does not import `litellm` at all.

Direct `litellm` imports in `src/luthien_proxy/` are limited to:

- **Judge-LLM calls** — `llm/judge_client.py`, `policies/simple_llm_utils.py`, `policies/tool_call_judge_utils.py` call `litellm.acompletion` to ask a judge model for a decision. `judge_client.judge_completion` is the common wrapper.
- **Startup config** — `main.py` sets `litellm.drop_params = True` once so judge calls tolerate unknown kwargs.
- **Admin model listing** — `admin/routes.py` reads `litellm.anthropic_models` for the admin UI model dropdown.

Indirect references (no import, still tied to the judge path): `exceptions.map_litellm_error_type()` maps LiteLLM exception class names to `BackendAPIError` codes via string lookup, and `settings.litellm_master_key` is retained as a legacy judge-key fallback.

See `dev/context/codebase_learnings.md` (LiteLLM Usage Boundaries) for the complete list.

## Troubleshooting

- **Stream hangs or truncates**: inspect `pipeline/anthropic_processor.py` `_handle_execution_streaming` and the policy's `on_anthropic_stream_event` / `on_anthropic_stream_complete` hooks — the SSE iterator pulls directly from whatever those hooks yield.
- **Protocol-ordering violations in logs**: `streaming.protocol_violation` events indicate the policy emitted events out of Anthropic's required order (all `content_block_*` must precede `message_delta`). See `gotchas.md` under "Anthropic Streaming: All Content Blocks Must Precede message_delta".
- **Empty stream 500**: the policy returned without yielding any events; `_handle_execution_streaming` converts that into an explicit Anthropic error event and logs `policy.execution.empty_stream`.
- **Judge LLM call failures**: these come from `judge_client.judge_completion`, not the main request path; check `LLM_JUDGE_API_KEY`, per-policy `api_key`, and the upstream LiteLLM configuration.

## Related Documentation

- `ARCHITECTURE.md` — broader module map (note: known staleness tracked separately on Trello; prefer the source code when in doubt).
- `dev/context/codebase_learnings.md` — architectural patterns.
- `dev/context/gotchas.md` — streaming protocol invariants and common debugging pitfalls.
- `dev/observability.md` — tracing, event recording, and Tempo integration.
