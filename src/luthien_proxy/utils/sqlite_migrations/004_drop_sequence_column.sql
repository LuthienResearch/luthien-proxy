-- ABOUTME: Drops the sequence column from conversation_events
-- ABOUTME: Events are now ordered by created_at timestamp instead

DROP INDEX IF EXISTS idx_conversation_events_call_sequence;
ALTER TABLE conversation_events DROP COLUMN sequence;
CREATE INDEX IF NOT EXISTS idx_conversation_events_call_created ON conversation_events(call_id, created_at);
