---
category: Fixes
pr: 595
---

**Missing retention index migration**: Added migration 015 (retention index) that was omitted from PR #571
  - Migrations 014 (user_id), 015 (retention index), and 016 (session search tsvector) now complete
  - Fixes database schema consistency for data retention features

**Migration 016 backfill note**: The tsvector backfill in `016_add_session_search_tsvector.sql` runs as a single transaction over all `transaction.request_recorded` rows. On large production databases this will produce lock contention and WAL bloat. Run during a maintenance window or with low traffic.
