---
category: Features
pr: 752
---

**Admin UI performance optimizations**: Cursor pagination and memory caps for the history page and conversation viewer.
  - Cursor-paginated infinite scroll on `/history` (20 sessions per page instead of all)
  - Conversation viewer loads full session JSON via `/api/history/sessions/{id}` and renders structured turns; paginated lazy loading of turns is deferred to a follow-up PR
  - Raw events capped at 50 per call_id (FIFO) to bound per-turn memory growth
  - Debounced server-side filter on `/history` to reduce query load
  - New fragment endpoints: `/ui/fragments/sessions`, `/ui/fragments/sessions/{id}/turns`
  - **Known limitation**: `filter=claude` uses a full-table payload scan (`payload LIKE '%claude-code%'`) with no index. It is correct for small deployments but will be slow on large Postgres instances. A structured `client_type` column or trigram index is the long-term fix.
  - **Known limitation**: session-ID search (`q=`) uses a leading-wildcard `LIKE '%q%'` which cannot use a btree index. Intended for small deployments; a trigram index or prefix-only match is the long-term fix.
