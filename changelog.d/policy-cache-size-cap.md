---
category: Features
---

**PolicyCache size cap with FIFO eviction**: `PolicyCache` now enforces an entry cap per policy namespace. When a `put()` would exceed the cap, the oldest entries (by `created_at`) are evicted in the same transaction, so the shared `policy_cache` table no longer grows unbounded between manual `cleanup_expired()` calls.
  - Default cap is 10,000 entries per `policy_name`; configure via `POLICY_CACHE_MAX_ENTRIES` (0 or negative disables the cap).
  - Eviction order: FIFO (`created_at ASC`, with `cache_key` as a deterministic tiebreak). Equivalent to LRU-by-insertion without the read-amplification cost of tracking last-access times.
  - Upsert and eviction run inside one transaction, so a refreshed key is safe from eviction and concurrent puts converge to the cap (soft under Postgres concurrent writers, hard under SQLite's serialized writes).
