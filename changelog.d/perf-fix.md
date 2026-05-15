---
category: Features
pr: 752
---

**Admin UI performance optimizations**: Cursor pagination, lazy loading, and memory caps for history and conversation pages.
  - Cursor-paginated infinite scroll on `/history` (20 sessions per page instead of all)
  - Lazy-loaded turns on `/conversation/live` (10 turns at a time instead of all)
  - Raw events memory cap at 50 events to prevent unbounded growth
  - Debounced server-side filter on `/history` to reduce query load
  - New fragment endpoints: `/ui/fragments/sessions`, `/ui/fragments/sessions/{id}/turns`
