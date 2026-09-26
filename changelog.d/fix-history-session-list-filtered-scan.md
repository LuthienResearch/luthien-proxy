---
category: Fixes
---

**History session list: filtered and user-scoped pages no longer aggregate every conversation event**
  - `GET /api/history/sessions` with `user_id`, `model`, `from`/`to`, `q` or
    `policy_intervention` re-aggregated the entire `conversation_events` table
    three times per page (stats, models, first message) and then applied
    `LIMIT/OFFSET`, so a 50-row page cost as much as the whole table — and
    the JSON `payload` reads detoasted every row. On the production Aurora
    cluster this pinned the writer at its ACU ceiling for ~15 hours
    (Performance Insights: 99.5 % of db.load on this statement, 87 %
    `IO:DataFileRead`).
  - The page of candidate sessions now comes from `session_summaries` (one
    row per session, indexed on `last_seen`): time bounds read `last_seen`,
    `policy_intervention` pre-gates on `policy_event_count` and confirms with a
    per-session probe, `model` / `q` probe only the candidate session's own
    events, and the user scope comes from `conversation_calls(user_id)`.
    `conversation_events` is then aggregated only for the sessions on the
    page. The filtered `total` is a count over the same candidates, no
    `GROUP BY` over events. Per-session stats, models and preview stay
    scoped to the requesting user's calls, and every user-supplied value is
    still bound, never interpolated.
  - Measured on a 200k-event / 5k-session Postgres 16 (`EXPLAIN (ANALYZE,
    BUFFERS)`, 50-row page): `model` + time range 1067 ms → 105 ms
    (315,853 → 31,426 event rows read); `policy_intervention` 989 ms → 49 ms
    (501,000 → 27,150); rare full-text term 1132 ms → 27 ms (303,620 →
    7,092); rare model 1155 ms → 7 ms (300,680 → 423); unfiltered 1411 ms →
    45 ms (501,000 → 11,000).
  - Behaviour change, shared sessions only: when a `session_id` is used by
    several users, `from`/`to` and the list order now follow the session's
    overall last activity rather than the requesting user's last event in it.
    The stats shown for the row are still that user's alone.
  - Pages are ordered `last_seen DESC, session_id DESC`; the explicit
    tiebreak keeps paging deterministic when two sessions share a timestamp.
