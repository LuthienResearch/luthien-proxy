---
category: Fixes
---

**FTS backfill computes search text once per row**: The Postgres `014_add_session_search_fts.sql` backfill called `_extract_event_search_text(payload)` twice per row (once in the `UPDATE SET` clause, once in the `WHERE` filter), doubling the per-row work over the whole `conversation_events` table. It now computes the value once in a subquery and reuses it for both the tsvector and the NULL filter; results are unchanged. (PR #614 review follow-up, GH #763)
