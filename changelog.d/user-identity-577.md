---
category: Features
---

**User identity extraction**: The proxy now records a `user_id` against
each conversation call so operators can attribute traffic to individual
users in the history UI and `GET /api/history/sessions?user_id=...`.

  - Source 1 (default off): `X-Luthien-User-Id` request header. Trusted
    only when `TRUST_USER_ID_HEADER=true` — leave disabled unless clients
    are behind an authenticated reverse proxy.
  - Source 2: the `sub` claim of a Bearer JWT, decoded **without signature
    verification**. Treat as user-asserted attribution, never as auth.
  - Stored in new columns on `conversation_calls` and `conversation_events`
    (migration 018), and surfaced on `SessionSummary.user_id`.
