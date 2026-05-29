---
category: Features
---

**Server-side session search**: `/api/history/sessions` now accepts `model`, `from`, `to`, `q` (full-text content), and `policy_intervention` filters, on top of the existing `user_id`. Resolves #558.
  - Builds on the existing dual-backend FTS infra (Postgres `tsvector` / SQLite FTS5) via `utils.search.session_fts_filter_sql` — no new migration.
  - `q` is porter-stemmed and term-conjunctive with parity across backends; `model`/`q` match if any turn matches, while time and intervention filters operate on session-level aggregates so per-session stats stay whole.
  - `total` in the response now reflects the filtered count (was always the global count). The unfiltered list path is unchanged.
