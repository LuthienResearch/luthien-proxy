---
category: Refactors
---

**policy_cache: drop unused `updated_at` column**: The `updated_at` column on the `policy_cache` table was written by every upsert but never read by any code path. Dropped it from the migration files and the `put()` SQL to avoid write amplification and lock in the YAGNI decision. Added schema regression tests that will fail if the column (or a SQL statement referencing it) is reintroduced without a consumer.
