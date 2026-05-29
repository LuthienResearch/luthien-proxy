---
category: Fixes
---

**Judge client: cached HTTP client + skip retries on permanent failures**
  - `DirectApiProvider` now reuses a cached HTTP client for stable-credential judge calls (via `anthropic_client_cache`) instead of building and closing a fresh client on every call. A judge that fires on every tool call in an agent loop no longer pays connection-pool init/teardown each time. Per-user passthrough (with `credential_override`) still builds and closes a fresh client per call.
  - `call_simple_llm_judge` no longer retries permanent failures: a rejected credential (`InferenceInvalidCredentialError`, 401/403) fails fast instead of burning `max_retries * retry_delay` seconds and hammering the upstream. Transient errors and parse failures still retry.
