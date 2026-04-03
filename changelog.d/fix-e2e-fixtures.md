---
category: Fixes
pr: 494
---

**Fix broken e2e tests and add single-command test runner**: Replace fragile module-level monkey-patching with pytest fixture overrides for e2e test config (gateway_url, api_key, auth_headers). Fixes 40 sqlite_e2e tests broken since PR #410, plus 5 mock_e2e test failures from hardcoded ports, stale Docker fallbacks, and import-time settings caching. Adds `scripts/run_e2e.sh` to orchestrate all e2e tiers (sqlite, mock, real) with automatic setup/teardown — no Docker needed for sqlite or mock tiers. All 210 tests (42 sqlite + 168 mock) now pass from a single `./scripts/run_e2e.sh sqlite mock` invocation.
