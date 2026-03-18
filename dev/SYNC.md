# Cross-Instance Sync

**Written by**: Claude Code instance 1 (session working on PR #343)
**Time**: 2026-03-17 ~20:40 PDT
**Branch**: `fix/policy-config-ui-and-judge-passthrough-auth`

---

## What has been done (all committed and pushed)

### Bug fixes
- `policy_discovery.py`: Fix `python_type_to_json_schema` — union containing Pydantic model + dict + None was falling back to `{"type": "string"}` instead of the structured Pydantic schema. This was breaking the SimpleLLMPolicy config UI (rendered as plain text input).
- `simple_llm_policy.py`: Remove redundant `| dict[str, Any]` from `__init__` signature.
- `policy_discovery.py`: Use `model_class.model_fields` to build `example_config` for Pydantic params — fixes `default_factory` fields (DogfoodSafetyPolicy `blocked_patterns` now pre-populates correctly).
- `simple_llm_utils.py`, `tool_call_judge_utils.py`: Improved `model` and `api_base` field descriptions.

### Passthrough auth for judge LLM calls
- `base_policy.py`: Added `_extract_passthrough_key()`, `_resolve_judge_api_key()`, `_judge_oauth_headers()` static methods.
- `simple_llm_policy.py`, `tool_call_judge_policy.py`: Per-request key resolution with priority: explicit policy key → passthrough (client's auth token) → server fallback.
- `simple_llm_utils.py`, `tool_call_judge_utils.py`: `api_key` and `extra_headers` params added to judge call functions.
- OAuth tokens get `anthropic-beta: oauth-2025-04-20` header automatically.

### Tests
- `test_base_policy_passthrough.py`: `_extract_passthrough_key` and `_judge_oauth_headers` unit tests
- `test_simple_llm_policy.py`: `TestResolveJudgeApiKey` priority chain tests
- `test_policy_discovery.py`: Union-with-Pydantic regression test + `TestPydanticModelDefaults`
- `test_mock_simple_llm_passthrough_auth.py`: Mock e2e — passthrough key forwarded to judge (streaming + non-streaming + explicit key override)
- `test_mock_simple_llm_oauth_passthrough.py`: Mock e2e — OAuth token + beta header, OAuth-only, OAuth > server env key, regular API key no beta header

### Docs
- `dev/context/authentication.md`: New doc covering auth modes, passthrough flow, judge key resolution

### Infrastructure
- `docker-compose.mock-bridge.yaml`: Mock e2e overlay using bridge networking (accessible from sandbox)
- `mock_anthropic/server.py`: Now captures request headers (`last_request_headers()`, `received_request_headers()`)
- `conftest.py`: Added `auth_config_context()` for temporarily changing auth mode in tests

---

## Current state

- PR #343 is open, latest review addressed (tests, narrowed except, rename, union warning)
- All unit tests passing (1697)
- All mock_e2e tests passing (5 passthrough + 4 OAuth)
- `dev_checks.sh` clean
- Only uncommitted change: `src/luthien_cli/uv.lock` (unrelated to this work)

## Remaining / open questions

- PR not yet marked ready (still draft?) — check `gh pr view 343`
- The `luthien_cli/uv.lock` change on the branch — needs a separate commit or squash
- Trello ticket created for multi-policy Pydantic conversion (to you, in "after March 19" column)

---

**If you're the other instance**: please read this, check `git log --oneline -5` to confirm you're up to date, and let the user know your current state. Avoid pushing to this branch without pulling first.
