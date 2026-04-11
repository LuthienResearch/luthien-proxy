---
category: Fixes
---

**Activity stream sqlite e2e fixture settings cache**: Follow-up to PR #538. The `gateway_url` module-scoped fixture in `tests/luthien_proxy/e2e_tests/sqlite/test_activity_stream.py` had the same stale-settings-cache bug: it set `ANTHROPIC_*` env vars then called `create_app()` without flushing `get_settings()`, and module-scoped fixtures run before the function-scoped autouse cache clearer. Now calls `clear_settings_cache()` before `create_app()` and again in teardown. Also added a teardown `clear_settings_cache()` to `sqlite/conftest.py::sqlite_gateway_url` for full hermeticity.
