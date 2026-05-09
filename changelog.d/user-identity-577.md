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
    (migration 018), and surfaced on `SessionSummary.user_ids` (a list,
    so sessions reused across users render honestly instead of attributing
    to one). Retention/purge tooling that scrubs PII should account for
    both columns.

**EventEmitter.dropped_db_writes** silently changed from a class-level
counter (shared across all instances) to a per-instance counter. The
class-level form was a latent bug (test isolation, multi-emitter scrape).
Anything reading `EventEmitter.dropped_db_writes` directly will now see 0;
read it from the instance instead.
