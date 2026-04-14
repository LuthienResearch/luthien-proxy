---
category: Features
---

**SQLite FTS5 + Postgres tsvector for conversation-event search**: Migration 014
adds Postgres `tsvector`/GIN infra and a SQLite `conversation_events_fts` FTS5
virtual table with a trigger that extracts user-message and assistant-response
text from the JSON payload. Callers use `session_fts_filter_sql()` to get a
dialect-correct predicate and never branch on the backend directly.
  - Fixes the SQLite migration runner to apply trigger-containing migrations via
    native `executescript`, preserving `BEGIN ... END` blocks.
