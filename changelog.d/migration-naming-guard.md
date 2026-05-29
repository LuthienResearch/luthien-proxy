---
category: Chores & Docs
---

**Migration naming guard**: Added `test_migration_naming.py` enforcing migration filename hygiene in the default test/`dev_checks` pass — `NNN_snake_case.sql` format, no *new* duplicate numeric prefixes (existing `008`/`014` collisions grandfathered, since the filename-keyed `_migrations` table makes renumbering applied history unsafe), and Postgres/SQLite prefix parity. Prevents recurrence of the silent prefix collisions where two branches grabbed the same migration number.
