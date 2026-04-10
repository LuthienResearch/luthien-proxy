---
category: Refactors
---

**MockAnthropicClient replaces HTTP mock backend for mock_e2e tests**: Inject a Python-level `MockAnthropicClient` directly into the gateway's dependency container instead of routing LLM calls through an HTTP mock server (`MockAnthropicServer` + `ANTHROPIC_BASE_URL`). The mock_e2e tier now runs with an in-process gateway (no subprocess, no port allocation for main LLM calls), fixing the port TOCTOU race and eliminating ~40 lines of subprocess orchestration from `run_e2e.sh`.
  - `MockAnthropicClient` satisfies the same duck-type interface as `AnthropicClient` and the same enqueue/inspect API as `MockAnthropicServer` — all 164 mock_e2e tests pass unchanged
  - LiteLLM `acompletion` calls (judge LLM) are also intercepted in-process; a companion `MockAnthropicServer` still runs for passthrough-auth tests that inspect HTTP headers
  - `tests/luthien_proxy/e2e_tests/mock/` replaces loose `test_mock_*.py` files in the parent directory
