# Pipeline Guide

## Scope

- This directory owns the Anthropic request lifecycle after routing hands off from `gateway_routes.py`.
- Start in `anthropic_processor.py`; it is the execution engine for auth-resolved `/v1/messages` traffic.

## Files that matter

| File | Why it matters |
| --- | --- |
| `anthropic_processor.py` | end-to-end request execution, policy IO, streaming/non-streaming handlers |
| `stream_protocol_validator.py` | Anthropic SSE ordering invariants |
| `policy_context_injection.py` | optional system-message injection of active policy names |
| `session.py` | session-id extraction from request body/headers |
| `client_format.py` | client format enum; currently Anthropic-only |

## Local rules

- Keep request lifecycle changes aligned with the four phases documented in `ARCHITECTURE.md`: ingest/auth, process request, execute policy, send to client.
- `_AnthropicPolicyIO` is request-scoped. Do not leak mutable request or backend-response state outside the active transaction.
- Preserve the distinction between pre-header failures (raise typed errors) and mid-stream failures (emit inline SSE error events).
- Streaming and non-streaming paths must stay behaviorally aligned for observability, history, and policy semantics.

## Streaming invariants

- Anthropic event ordering is strict: `message_start` first, `message_stop` last, content-block lifecycle in between.
- `content_block_*` events must finish before `message_delta` / stop metadata.
- History reconstruction depends on event sequences staying valid; a “small” SSE change can break exports and the history UI.
- If you change event emission shape, update or add deterministic `mock_e2e` coverage instead of relying only on real API tests.

## Common traps

- Filtering or transforming stream events without re-validating the resulting sequence.
- Changing request parsing without preserving `RawHttpRequest`, session id extraction, or request logging behavior.
- Forgetting that observability events, request logs, and usage telemetry are emitted from this path too.
- Treating `policy_context_injection` as cosmetic; it changes the actual backend request body.

## Verification targets

- Unit tests for isolated helper behavior.
- `mock_e2e` for streaming structure and SSE ordering.
- `sqlite_e2e` or integration coverage when request flow touches persistence or history reconstruction.
- Run `./scripts/dev_checks.sh` before pushing any pipeline change.
