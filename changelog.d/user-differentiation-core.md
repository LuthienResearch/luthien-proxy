---
category: Features
---

**User differentiation in the history viewer**: Attribute and label traffic per user on shared deployments.
  - `request_logs` now carries `user_id` (mirroring `conversation_calls`), with a `user_id` filter on the request-logs API and UI.
  - New `session_summaries` materialized table, maintained incrementally on each event write (counts, models used, message preview, attributed `user_id`), so the history list does not re-aggregate `conversation_events`.
  - New `user_labels` table mapping `user_id` to a display name, with `/api/history/users` and `/api/history/user-labels` endpoints (list / set / delete).
  - History UI: per-user filter dropdown, deterministic-colored user badges on session cards, and click-a-badge to assign or clear a display name.
