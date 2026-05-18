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
  - Stored in a new column on `conversation_calls` (migration 018), and
    surfaced on `SessionSummary.user_ids` (a list, so sessions reused across
    users render honestly instead of attributing to one). Retention/purge
    tooling that scrubs PII should account for this column.
  - Also captured as a `luthien.user_id` OpenTelemetry span attribute and
    logged at DEBUG (`Extracted user_id: ...`). For deployments where
    `user_id` is an email or other PII, those sinks see it too — flip
    `TRUST_USER_ID_HEADER` and/or accept JWT Bearers only with that in mind.
