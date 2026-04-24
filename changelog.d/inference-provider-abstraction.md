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
  - No callsite changes yet; the registry, YAML rename, and policy-testing
    UI integration come in follow-up PRs.
