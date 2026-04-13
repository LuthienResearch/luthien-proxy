-- ABOUTME: Adds user_id column to conversation_calls and conversation_events
-- ABOUTME: Enables tracking which user made each request for team deployments
-- ABOUTME: Extracted from X-Luthien-User-Id header or JWT Bearer token sub claim

-- Add user_id to conversation_calls (one row per API call)
ALTER TABLE conversation_calls ADD COLUMN user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_conversation_calls_user ON conversation_calls(user_id) WHERE user_id IS NOT NULL;

-- Add user_id to conversation_events (one row per event within a call)
ALTER TABLE conversation_events ADD COLUMN user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_conversation_events_user ON conversation_events(user_id) WHERE user_id IS NOT NULL;
