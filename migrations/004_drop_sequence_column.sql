-- ABOUTME: Drops the sequence column from conversation_events
-- ABOUTME: Events are now ordered by created_at timestamp instead

-- Drop the index that used sequence
DROP INDEX IF EXISTS idx_conversation_events_call_sequence;

-- Drop the sequence column
ALTER TABLE conversation_events DROP COLUMN IF EXISTS sequence;

-- Add index on (call_id, created_at) for ordering queries
CREATE INDEX IF NOT EXISTS idx_conversation_events_call_created ON conversation_events(call_id, created_at);
