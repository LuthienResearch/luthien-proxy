---
category: Chores & Docs
pr: 597
---

**Remove deprecated UI redirect routes**: Dropped legacy backwards-compat redirects with no internal callers.
  - `GET /activity/monitor` (previously redirected to `/history`)
  - `GET /debug/diff` (previously redirected to `/diffs`)
  - `GET /history/session/{session_id}` (previously redirected to `/conversation/live/{session_id}`)
