---
category: Fixes
---

**`create_app` refuses to boot an unauthenticated admin surface**: if `ADMIN_API_KEY` is unset while `LOCALHOST_AUTH_BYPASS` is disabled (a network-exposed deployment), the gateway now raises at construction instead of serving the admin/history UI without authentication. Defense-in-depth — shipped entry points already avoid this via `auto_provision_defaults()`, but the invariant is now pinned at the factory so any entry point is covered. (Supersedes the role-separation approach explored in #574/#772; see those for the rationale.)
