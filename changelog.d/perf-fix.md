---
category: Features
pr: 752
---

**Admin UI performance optimizations**: Cursor pagination, lazy loading, and memory caps for history and conversation pages.
  - Cursor-paginated infinite scroll on `/history` (20 sessions per page instead of all)
  - Lazy-loaded turns on `/conversation/live` via JSON API + structured turn rendering
  - Raw events capped at 50 per call_id (FIFO) to bound per-turn memory growth
  - Debounced server-side filter on `/history` to reduce query load
  - New fragment endpoints: `/ui/fragments/sessions`, `/ui/fragments/sessions/{id}/turns`
  - **Known limitation**: `filter=claude` uses a full-table payload scan (`payload LIKE '%claude-code%'`) with no index. It is correct for small deployments but will be slow on large Postgres instances. A structured `client_type` column or trigram index is the long-term fix.
