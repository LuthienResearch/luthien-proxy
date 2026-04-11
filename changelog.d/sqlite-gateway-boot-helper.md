---
category: Refactors
---

**Sqlite e2e gateway boot helper**: Extracted the in-process SQLite gateway boot/teardown logic into `tests/luthien_proxy/e2e_tests/sqlite/_boot.py::boot_sqlite_gateway`. Both `sqlite/conftest.py::sqlite_gateway_url` (session-scoped) and `sqlite/test_activity_stream.py::gateway_url` (module-scoped) now share the same code path — eliminating ~140 lines of near-identical scaffolding and the drift risk that caused #539 (a stale-settings-cache fix that had to be applied separately to each copy after #538 only patched one). Also closes a pre-existing `tmp_dir` leak in `test_activity_stream.py` teardown that the conftest copy already handled.
