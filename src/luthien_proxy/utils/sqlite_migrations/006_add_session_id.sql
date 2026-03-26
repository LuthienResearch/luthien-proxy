-- ABOUTME: Adds session_id column to conversation_events for tracking agent sessions
-- ABOUTME: Enables querying conversations by session

ALTER TABLE conversation_events ADD COLUMN session_id TEXT;
CREATE INDEX IF NOT EXISTS idx_conversation_events_session ON conversation_events(session_id) WHERE session_id IS NOT NULL;

ALTER TABLE conversation_calls ADD COLUMN session_id TEXT;
CREATE INDEX IF NOT EXISTS idx_conversation_calls_session ON conversation_calls(session_id) WHERE session_id IS NOT NULL;
