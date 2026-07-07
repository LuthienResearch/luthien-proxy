---
category: Fixes
---

**Fix admin dashboard slowness from response payload bloat**: the session-detail
API no longer re-sends the full cumulative conversation history inside every
turn (response payload is now O(total messages) instead of O(turns^2); the
dedup the activity-monitor frontend previously did client-side moved
server-side via `request_delta_start`), and the session-list queries fetch
preview/model payloads only for the page's sessions instead of probing every
stored request payload in the table. Markdown/JSONL exports also stop
repeating prior turns' history inside each turn.
