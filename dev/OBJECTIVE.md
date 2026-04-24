# Objective: InferenceProvider interface + ClaudeCode/DirectApi backends

PR #2 of a 5-PR inference-provider initiative.

## Scope

Introduce a server-side `InferenceProvider` abstraction for proxy-originated LLM
calls (judges, policy-testing, any future proxy-internal inference). Server-side
inference bypasses the gateway and active policy — it is proxy-defined logic
that policies may depend on, so looping through the policy pipeline would create
circular dependencies.

Deliverables:

1. **`InferenceProvider` abstract base** at `src/luthien_proxy/inference/base.py`
   with a `complete()` async method and typed `InferenceError` hierarchy.
2. **`ClaudeCodeProvider`** at `src/luthien_proxy/inference/claude_code.py` —
   spawns `claude -p --bare` as a subprocess, auths via an OAuth access token
   stored as a named `Credential`.
3. **`DirectApiProvider`** at `src/luthien_proxy/inference/direct_api.py` —
   wraps `llm.judge_client.judge_completion` (reuse, don't duplicate). Handles
   both configured server creds and `credential_override` passthrough.
4. Unit tests mirroring the package layout under
   `tests/luthien_proxy/unit_tests/inference/`.
5. Changelog fragment at `changelog.d/inference-provider-abstraction.md`.

## Out of scope

- Provider registry / DB table / admin API / UI (PR #3).
- Callsite changes — `judge_client.py` and its callers keep working exactly
  as they do today.
- Policy YAML schema changes (`auth_provider:` → `inference_provider:`) (PR #4).
- Policy-testing UI changes (PR #5).
- Deleting `judge_client.py`.

## Acceptance

- `./scripts/dev_checks.sh` passes.
- New unit tests pass and mock the subprocess for `ClaudeCodeProvider`.
- An optional integration test (skipped in CI unless `LUTHIEN_TEST_CLAUDE=1`)
  does an end-to-end `claude -p` call.
- PR opens as draft with a summary linking to the 5-PR plan and to the
  `dev/NOTES.md` claude-p investigation findings.
