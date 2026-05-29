-- ABOUTME: Materialized per-session summary maintained incrementally on each event write.
-- ABOUTME: Denormalizes counts, models_used, and preview_message so the history
-- ABOUTME: list page does not re-aggregate conversation_events on every load.

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    call_count INTEGER NOT NULL DEFAULT 0,
    policy_event_count INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    models_used TEXT,
    preview_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_last_seen ON session_summaries(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_session_summaries_user ON session_summaries(user_id) WHERE user_id IS NOT NULL;

-- Backfill counts + user_id from existing data.
-- preview_message is intentionally NOT backfilled: extracting the first
-- non-probe user message from JSON payloads is expensive and fragile in SQL.
-- The event-write path populates it incrementally for new events; existing
-- sessions show no preview until they receive a new event.
INSERT INTO session_summaries (
    session_id, first_seen, last_seen, event_count, call_count, policy_event_count, user_id, models_used
)
SELECT
    ce.session_id,
    MIN(ce.created_at),
    MAX(ce.created_at),
    COUNT(*),
    -- call_count counts request-recorded events (one per call), matching the
    -- incremental maintenance in observability/session_summary.py.
    COUNT(*) FILTER (WHERE ce.event_type = 'transaction.request_recorded'),
    COUNT(*) FILTER (
        WHERE ce.event_type LIKE 'policy.%'
        AND ce.event_type NOT LIKE 'policy.judge.evaluation%'
    ),
    -- Earliest-call user_id wins, matching the incremental COALESCE semantics
    -- (first non-null user_id seen for the session sticks). ORDER BY makes the
    -- pick deterministic when a session spans multiple users.
    (SELECT cc.user_id FROM conversation_calls cc
       WHERE cc.session_id = ce.session_id AND cc.user_id IS NOT NULL
       ORDER BY cc.created_at LIMIT 1),
    (SELECT string_agg(DISTINCT m.model, ',')
       FROM (
         SELECT ce2.payload->>'final_model' AS model
         FROM conversation_events ce2
         WHERE ce2.session_id = ce.session_id
           AND ce2.event_type = 'transaction.request_recorded'
           AND ce2.payload->>'final_model' IS NOT NULL
       ) m)
FROM conversation_events ce
WHERE ce.session_id IS NOT NULL
GROUP BY ce.session_id
ON CONFLICT (session_id) DO NOTHING;
