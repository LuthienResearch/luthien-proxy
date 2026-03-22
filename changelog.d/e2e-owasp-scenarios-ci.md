---
category: Chores & Docs
---

**mock_e2e tests added to CI**: `dev-checks.yaml` now runs the full mock_e2e suite (166 tests) on every PR using a dockerless gateway with SQLite. Previously mock_e2e was excluded from all automated checks.
  - Fixed 8 pre-existing failures: judge passthrough auth tests now use `MOCK_ANTHROPIC_HOST` env var; `_enable_request_logging` fixture is a no-op when `ENABLE_REQUEST_LOGGING` is already set
  - Fixed judge API key resolution in real-API tests: explicit `api_key` in policy config takes priority over passthrough bearer token
