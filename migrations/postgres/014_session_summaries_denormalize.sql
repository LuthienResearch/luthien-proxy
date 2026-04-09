-- ABOUTME: Denormalizes models_used and preview_message into session_summaries
-- ABOUTME: Adds composite index on conversation_events for faster filtered queries

-- Composite index: covers the common WHERE session_id + event_type pattern
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversation_events_session_type
    ON conversation_events(session_id, event_type)
    WHERE session_id IS NOT NULL;

-- Add denormalized columns to session_summaries
ALTER TABLE session_summaries ADD COLUMN IF NOT EXISTS models_used TEXT;
ALTER TABLE session_summaries ADD COLUMN IF NOT EXISTS preview_message TEXT;

-- Backfill models_used from conversation_events
UPDATE session_summaries ss
SET models_used = sub.models
FROM (
    SELECT
        session_id,
        string_agg(DISTINCT payload->>'final_model', ',') as models
    FROM conversation_events
    WHERE session_id IS NOT NULL
    AND event_type = 'transaction.request_recorded'
    AND payload->>'final_model' IS NOT NULL
    GROUP BY session_id
) sub
WHERE ss.session_id = sub.session_id
AND ss.models_used IS NULL;
