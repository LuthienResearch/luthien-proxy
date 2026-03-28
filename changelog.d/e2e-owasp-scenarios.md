---
category: Features
---

**OWASP threat scenario e2e tests**: 48 new mock_e2e tests covering LLM01 (Prompt Injection), LLM06 (Sensitive Disclosure), LLM08 (Excessive Agency), gateway robustness, and audit trail. 7 real-API tests with retry and failure capture for non-deterministic judge validation.
  - OWASP LLM markers (llm01/02/04/06/07/08) added to pytest config
  - `FailureCapture` fixture: on real-API test failure, writes actual judge response to `failure_registry/` for analysis
  - `scripts/generate_mock_from_failures.py`: converts failure captures into deterministic mock regression tests
