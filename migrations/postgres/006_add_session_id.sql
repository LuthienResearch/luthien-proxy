-- ABOUTME: Adds session_id column to conversation_events for tracking agent sessions
-- ABOUTME: Enables querying conversations by session (e.g., all calls from same Claude Code session)

-- Add session_id column to conversation_events
ALTER TABLE conversation_events ADD COLUMN IF NOT EXISTS session_id TEXT;

-- Add index for querying by session
CREATE INDEX IF NOT EXISTS idx_conversation_events_session ON conversation_events(session_id) WHERE session_id IS NOT NULL;

-- Add session_id to conversation_calls as well for quick session lookups
ALTER TABLE conversation_calls ADD COLUMN IF NOT EXISTS session_id TEXT;

-- Add index for querying calls by session
CREATE INDEX IF NOT EXISTS idx_conversation_calls_session ON conversation_calls(session_id) WHERE session_id IS NOT NULL;
