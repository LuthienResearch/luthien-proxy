-- Materialized session summaries for fast history page queries.
-- Replaces the expensive GROUP BY over conversation_events.
-- Updated incrementally by the EventEmitter drain loop.

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    call_count INTEGER NOT NULL DEFAULT 0,
    policy_event_count INTEGER NOT NULL DEFAULT 0,
    user_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_last_seen
    ON session_summaries(last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_session_summaries_user_hash
    ON session_summaries(user_hash);

-- Backfill from existing data
INSERT OR IGNORE INTO session_summaries (session_id, first_seen, last_seen, event_count, call_count, policy_event_count, user_hash)
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
    (SELECT cc.user_hash FROM conversation_calls cc WHERE cc.session_id = ce.session_id AND cc.user_hash IS NOT NULL LIMIT 1)
FROM conversation_events ce
WHERE ce.session_id IS NOT NULL
GROUP BY ce.session_id;
