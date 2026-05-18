---
category: Refactors
---

**Require `auth_provider` for judge-using policies**: `SimpleLLMPolicy` and
`ToolCallJudgePolicy` now require an explicit `auth_provider` in their config.
The legacy per-policy `api_key` field and implicit passthrough/env-key fallback
("Step 5b" code path) have been removed. Shipped configs
(`config/railway_policy_config.yaml`, `config/policy_config.yaml`) and all
bundled presets declare `auth_provider: "user_credentials"` to preserve
existing OAuth-passthrough behavior. `call_judge()` from
`tool_call_judge_utils` and the `_extract_passthrough_key` / `_resolve_judge_api_key`
helpers on `BasePolicy` are gone.
