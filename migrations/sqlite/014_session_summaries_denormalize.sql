-- ABOUTME: Denormalizes models_used and preview_message into session_summaries
-- ABOUTME: Adds composite index on conversation_events for faster filtered queries

-- Composite index: covers the common WHERE session_id + event_type pattern
CREATE INDEX IF NOT EXISTS idx_conversation_events_session_type
    ON conversation_events(session_id, event_type);

-- Add denormalized columns to session_summaries
ALTER TABLE session_summaries ADD COLUMN models_used TEXT;
ALTER TABLE session_summaries ADD COLUMN preview_message TEXT;

-- Backfill models_used from conversation_events
UPDATE session_summaries
SET models_used = (
    SELECT GROUP_CONCAT(DISTINCT json_extract(payload, '$.final_model'))
    FROM conversation_events ce
    WHERE ce.session_id = session_summaries.session_id
    AND ce.event_type = 'transaction.request_recorded'
    AND json_extract(payload, '$.final_model') IS NOT NULL
)
WHERE models_used IS NULL;
