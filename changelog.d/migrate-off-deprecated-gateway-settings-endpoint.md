---
category: Refactors
---

**Remove deprecated `/api/admin/gateway/settings` endpoint**: The policy config UI now reads/writes `inject_policy_context` and `dogfood_mode` via the canonical `/api/admin/config` and `/api/admin/config/{key}` endpoints. The superseded `GET`/`PUT /api/admin/gateway/settings` handlers have been deleted.
