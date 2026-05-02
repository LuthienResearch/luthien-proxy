---
category: Fixes
pr: 616
---

**SQLite test fixture**: add `SqliteConnection.executescript()` public method and use it in `sqlite_pool` fixtures to correctly handle migration files containing semicolons inside SQL comments or `BEGIN...END` trigger bodies.
