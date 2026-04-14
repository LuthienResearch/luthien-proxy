---
category: Chores & Docs
---

**DatabasePool test construction idiom**: Documented the canonical pattern for tests that need an in-memory SQLite `DatabasePool` with pre-populated schema (construct via the public constructor, prime with `get_pool()`, seed schema on the returned pool). Added a regression test in `tests/luthien_proxy/unit_tests/utils/test_db.py` that pins the pattern so downstream tests don't reach for `DatabasePool.__new__(...)` + private-attribute pokes.
