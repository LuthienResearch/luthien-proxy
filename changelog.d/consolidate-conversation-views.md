---
category: Refactors
pr: 501
---

**Consolidate conversation views**: Merged `/activity/monitor`, `/history/session/{id}`, and `/conversation/live/{id}` into a single live conversation viewer at `/conversation/live/{id}`
  - Session list at `/history` now links directly to the live view
  - Live view renders turns incrementally (new turns slide in without re-rendering existing ones)
  - Raw event stream viewer preserved at `/debug/activity` for low-level debugging
  - Old URLs (`/activity/monitor`, `/history/session/{id}`) 301-redirect to their replacements
