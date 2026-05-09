-- ABOUTME: Adds user_id column to conversation_calls and conversation_events
-- ABOUTME: Enables tracking which user made each request for team deployments
-- ABOUTME: Extracted from X-Luthien-User-Id header or JWT Bearer token sub claim

-- user_id on conversation_calls (one row per API call). This is the column the
-- history API filters/joins against; it gets a partial index for fast lookup.
ALTER TABLE conversation_calls ADD COLUMN IF NOT EXISTS user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_conversation_calls_user ON conversation_calls(user_id) WHERE user_id IS NOT NULL;

-- user_id on conversation_events is denormalized for analytical queries that
-- want per-event attribution (e.g. "raw client_request payloads for user X")
-- without a join. It is intentionally NOT indexed: every event row gets the
-- write cost, and the existing query paths all join through conversation_calls.
-- Add an index when an actual query path filters on conversation_events.user_id.
ALTER TABLE conversation_events ADD COLUMN IF NOT EXISTS user_id TEXT;
