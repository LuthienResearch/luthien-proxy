-- ABOUTME: Materialized per-session summary maintained incrementally on each event write.
-- ABOUTME: Denormalizes counts, models_used, and preview_message so the history
-- ABOUTME: list page does not re-aggregate conversation_events on every load.

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    call_count INTEGER NOT NULL DEFAULT 0,
    policy_event_count INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    models_used TEXT,
    preview_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_last_seen ON session_summaries(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_session_summaries_user ON session_summaries(user_id);

-- Backfill counts + user_id + models_used from existing data.
-- preview_message is intentionally NOT backfilled (see Postgres migration).
INSERT OR IGNORE INTO session_summaries (
    session_id, first_seen, last_seen, event_count, call_count, policy_event_count, user_id, models_used
)
SELECT
    ce.session_id,
    MIN(ce.created_at),
    MAX(ce.created_at),
    COUNT(*),
    COUNT(DISTINCT ce.call_id),
    SUM(CASE
        WHEN ce.event_type LIKE 'policy.%'
        AND ce.event_type NOT LIKE 'policy.judge.evaluation%'
        THEN 1 ELSE 0
    END),
    (SELECT cc.user_id FROM conversation_calls cc WHERE cc.session_id = ce.session_id AND cc.user_id IS NOT NULL LIMIT 1),
    (SELECT GROUP_CONCAT(DISTINCT json_extract(ce2.payload, '$.final_model'))
       FROM conversation_events ce2
       WHERE ce2.session_id = ce.session_id
         AND ce2.event_type = 'transaction.request_recorded'
         AND json_extract(ce2.payload, '$.final_model') IS NOT NULL)
FROM conversation_events ce
WHERE ce.session_id IS NOT NULL
GROUP BY ce.session_id;
