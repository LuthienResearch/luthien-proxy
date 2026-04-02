---
category: Fixes
pr: 494
---

**Fix broken sqlite_e2e tests and add e2e runner script**: Replace fragile module-level monkey-patching with pytest fixture overrides for e2e test config (gateway_url, api_key, auth_headers). Fixes 40 sqlite_e2e tests broken since PR #410. Adds `scripts/run_e2e.sh` to orchestrate e2e test tiers with automatic setup/teardown — no Docker needed for sqlite or mock tiers.
