---
category: Features
---

**Before/After preview backend for the admin policy-test endpoint**:
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
