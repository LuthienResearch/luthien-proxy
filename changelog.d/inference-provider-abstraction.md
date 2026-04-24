---
category: Features
pr: 605
---

**Server-side `InferenceProvider` abstraction**: introduce a named inference
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
    processes with OAuth tokens in their environment.
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
    UI integration come in follow-up PRs.
