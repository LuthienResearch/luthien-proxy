---
category: Fixes
pr: 811
---

**Add missing index on `request_logs.created_at`**: `request_logs` had no
index on `created_at`, so any time-windowed query against it (notably the
capture-liveness monitor's "any rows in the last 15 minutes" check, run every
5 minutes) forced a full-table `Parallel Seq Scan` -- a fixed, ever-growing
cost independent of live traffic. Adds `idx_request_logs_created_at` via
`CREATE INDEX CONCURRENTLY` so it can build against the live production table
without blocking writes.
