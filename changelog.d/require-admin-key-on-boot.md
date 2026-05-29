---
category: Fixes
---

**Admin UI fails closed when `ADMIN_API_KEY` is unset**: `check_auth_or_redirect` previously returned `None` ("authenticated") when no admin key was configured, so the admin/history UI routes (`/history`, `/config`, `/credentials`, …) were served without authentication. They now redirect to login (deny), matching the admin API path (`verify_admin_token`, which already rejects when the key is unset). Localhost-bypass is unchanged, so dockerless local dev still works. (Supersedes the role-separation approach explored in #574/#772.)
