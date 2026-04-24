---
category: Chores & Docs
---

**E2E regression coverage for the admin config dashboard PUT endpoint**: Add `test_mock_admin_config.py` — a mock_e2e suite that exercises the full `GET /api/admin/config` → `PUT /api/admin/config/{key}` → `GET` → `DELETE /api/admin/config/{key}` round-trip that the `/config` dashboard UI performs. Also pins 404 for unknown keys and 400 for non-db-settable fields. Closes the coverage gap left by #602 removing the deprecated `/gateway/settings` endpoint; surfaced the env-layer regression fixed in the parent PR.
