---
category: Refactors
---

**Dual SQLite/Postgres migration sync**: Replace the hand-maintained SQLite schema snapshot with incremental per-dialect migration files. SQLite migrations now run incrementally (matching Postgres behavior), with automatic bootstrap for existing databases. A CI schema comparison test catches drift between the two dialects.
