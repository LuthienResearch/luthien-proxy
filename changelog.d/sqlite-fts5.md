---
category: Features
---

**SQLite FTS5 + Postgres tsvector for conversation-event search**: Migration 014
adds Postgres `tsvector`/GIN infra and a SQLite `conversation_events_fts` FTS5
virtual table. Both backends use English stemming (Postgres
`plainto_tsquery('english', ...)`, SQLite FTS5 `tokenize='porter'`) so the same
query returns comparable hits on either dialect. Triggers on
`conversation_events` keep the FTS table in sync on INSERT and DELETE
(including CASCADE deletes from `conversation_calls`).
  - Helper `session_fts_filter_sql(pool, query, *, placeholder)` returns both a
    dialect-correct SQL fragment and a sanitized bind value. On SQLite the
    helper escapes FTS5 meta-characters (``'``, ``-``, ``+``, ``"``, ``:``) by
    quoting each whitespace-separated token as a phrase, preventing MATCH
    syntax errors and matching the conjunction-of-terms semantics of
    `plainto_tsquery`.
  - Fixes the SQLite migration runner to apply trigger-containing migrations
    via native `executescript`, preserving `BEGIN ... END` blocks. Adds a
    startup guard (unit test) that rejects any future SQLite migration file
    using Postgres-only syntax (`$N`, `::type`, `NOW()`, `ILIKE`, `LEAST`,
    `to_timestamp`, etc.) since `executescript` bypasses the runtime
    translator.
