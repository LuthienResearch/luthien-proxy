---
category: Features
pr: 607
---

**Inference provider registry**: Added a DB-backed registry for named
`InferenceProvider` instances with admin API and `/inference-providers`
UI, matching the operator workflow already used for server credentials.
  - New `inference_providers` table (postgres + sqlite migration 014).
  - `POST` / `GET` / `DELETE` at `/api/admin/inference-providers`.
  - Providers cached with a 60s TTL, dispatched on `backend_type` via a
    constructor map. Unknown backend types raise a typed error rather
    than failing deep in provider code.
  - `credential_name` is a soft reference — cred deletion surfaces as
    a clear error at `get()` time rather than cascading.
