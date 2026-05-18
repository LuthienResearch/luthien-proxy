---
category: Fixes
---

**dev_checks: unit tests no longer hang or error**:
  - `tests/luthien_proxy/unit_tests/inference/test_registry.py` fixture used a naive `sql.split(";")` to apply migrations, which shredded trigger bodies in `014_add_session_search_fts.sql` and failed with `near "the": syntax error`. Now reuses `_apply_sqlite_migrations()` (same runner used in production), which understands `BEGIN…END` blocks via `executescript()`.
  - `tests/luthien_cli/test_claude.py::test_claude_fails_when_not_installed` was not patching `ensure_gateway_up`, so it polled the real gateway health endpoint until pytest's timeout — making the unit-test phase appear to hang. Now patched consistently with the other tests in the file.
