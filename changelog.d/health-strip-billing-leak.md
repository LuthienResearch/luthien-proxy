---
category: Fixes
---

**Stop leaking auth-mode and credential activity from `/health`**: The unauthenticated `/health` endpoint previously returned `auth_mode`, `last_credential_type`, and `last_credential_at`, which a probe attacker could use to fingerprint the gateway's auth configuration and recent credential activity. Those fields now live on a new authenticated endpoint, `GET /api/admin/billing-status`, and the admin UI's nav badge fetches from there. `/health` is now `{status, version}` only.
