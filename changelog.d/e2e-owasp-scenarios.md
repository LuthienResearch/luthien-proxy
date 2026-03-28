---
category: Tests
pr: 458
---

**OWASP threat scenario e2e tests**: 48 new mock_e2e tests covering LLM01 (Prompt Injection), LLM06 (Sensitive Disclosure), LLM08 (Excessive Agency), gateway robustness, and audit trail.
  - OWASP LLM markers (llm01/02/04/06/07/08) added to pytest config for selective test runs
  - Fixed 4 pre-existing test failures: passthrough auth tests now use `MOCK_ANTHROPIC_HOST` env var (defaults to `host.docker.internal`, overridable to `localhost` for dockerless/CI runs); `_enable_request_logging` fixture is a no-op when `ENABLE_REQUEST_LOGGING` is already set in the environment
