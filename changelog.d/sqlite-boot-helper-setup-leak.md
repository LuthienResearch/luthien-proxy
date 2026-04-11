---
category: Fixes
---

**Sqlite e2e boot helper setup-time leak**: `boot_sqlite_gateway` (added in #541) only ran cleanup if execution reached `yield` — a raise from `check_migrations()`, `create_app()`, or the 10-second gateway-startup wait would leak the tmp_dir, env-var modifications, settings cache state, and (for the startup-wait case) the uvicorn thread. Rewrote the helper to use `contextlib.ExitStack`, registering each rollback as the matching resource is acquired, so any setup failure tears down only what was actually set up. Pre-existing in both pre-#541 fixture copies; centralizing them made it fixable in one place. Added regression tests in `tests/luthien_proxy/e2e_tests/sqlite/test_boot_helper.py` covering the `create_app` and `check_migrations` failure paths.
