---
category: Refactors
pr: 776
---

**Migrate judges to InferenceProvider, retire LiteLLM**:
  - Policy YAML field `auth_provider:` renamed to `inference_provider:`; old name still parses and logs a deprecation warning.
  - Judge policies (`SimpleLLMPolicy`, `ToolCallJudgePolicy`) now resolve their inference target through `luthien_proxy.inference.dispatch.resolve_inference_provider`, which dispatches on `UserCredentials` / `Provider(name)` / `UserThenProvider(name, on_fallback)`.
  - `DirectApiProvider` swapped from LiteLLM to the Anthropic SDK; structured outputs now use Anthropic's native tool-use (single forced tool with the caller-supplied schema).
  - Removed `litellm` dependency, `LITELLM_MASTER_KEY` / `LLM_JUDGE_API_KEY` config fields, `map_litellm_error_type`, and `luthien_proxy/llm/judge_client.py`.
  - `boto3` (used by the opt-in S3 conversation archiver) previously arrived transitively via `litellm[proxy]`. It is now a direct dependency, so archiving keeps working for every install with no operator action.
