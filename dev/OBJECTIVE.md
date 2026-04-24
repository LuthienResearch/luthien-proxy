# Objective: Migrate judges to InferenceProvider, retire LiteLLM

PR #4 of a 5-PR inference-provider initiative. Stacked on PR #607 (→ #605).

## Scope

Fold four concerns into one PR because they're coupled — judges import LiteLLM
today, so LiteLLM can't be removed until judges stop importing it.

1. **YAML rename:** Policy config field `auth_provider:` → `inference_provider:`
   with a legacy alias (log deprecation warning on the old name). Shape mirrors
   Jai's Model A (`inference_provider: user_credentials` | `{provider: "name"}`
   | `{user_then_provider: {name, on_fallback}}`).
2. **Judge migration:** `simple_llm_policy` / `simple_llm_utils` /
   `tool_call_judge_policy` / `tool_call_judge_utils` stop importing LiteLLM
   and route judge calls through `InferenceProviderRegistry.get(name)` +
   per-request user-credential passthrough, via a small dispatcher in
   `luthien_proxy.inference.dispatch`.
3. **DirectApiProvider internals:** Replace LiteLLM with the existing
   `AnthropicClient` wrapper. Translate `anthropic.APIStatusError` /
   `APITimeoutError` / `APIConnectionError` → `InferenceError` hierarchy.
4. **Delete:** `luthien_proxy/llm/judge_client.py`. Remove `map_litellm_error_type`,
   `litellm_master_key`, remaining litellm configs, uv deps, and all litellm
   references in src/ and tests/.

## Out of scope

- `/ping` endpoint on providers (PR #5).
- Policy-test UI provider picker (PR #5).
- Non-Anthropic backends (the judge flow now goes through AnthropicClient only).

## Acceptance

- `inference_provider:` is the preferred YAML field on judge configs;
  `auth_provider:` still parses with a deprecation warning.
- Judge policies work end-to-end with all three dispatch branches
  (`user_credentials`, named provider, user_then_provider × on_fallback).
- Anthropic structured outputs work via tool-use single-tool pattern when
  a schema is supplied, falling back to prompt-level instruction when no
  schema is needed.
- `rg -n "litellm" src/ tests/ pyproject.toml config/` returns zero lines.
- Gateway starts, registered provider + user-cred flow both work.
- `dev_checks.sh` is clean.
- Changelog fragment added.
