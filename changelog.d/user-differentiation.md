---
category: Features
pr: 580
---

**User differentiation**: Extract stable user hashes from request metadata, propagate through the event pipeline, and surface per-user filtering, badges, and display-name labeling in the history UI
  - Materialized `session_summaries` table eliminates expensive JOINs for the history page
  - Bounded EventEmitter with queue-based drain loop replaces per-event DB writes
  - SSE activity monitor throttled to max 10 updates/sec
