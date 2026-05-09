-- ABOUTME: Adds user_id column to conversation_calls and conversation_events
-- ABOUTME: Enables tracking which user made each request for team deployments
-- ABOUTME: Extracted from X-Luthien-User-Id header or JWT Bearer token sub claim

-- user_id on conversation_calls is the column the history API filters/joins against.
-- No IF NOT EXISTS on ALTER TABLE ADD COLUMN — migration runner guarantees
-- each migration runs exactly once, so idempotency is handled by the tracker.
ALTER TABLE conversation_calls ADD COLUMN user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_conversation_calls_user ON conversation_calls(user_id) WHERE user_id IS NOT NULL;

-- user_id on conversation_events is denormalized for analytical queries; not indexed
-- (every event row pays the write cost; current code joins through conversation_calls).
ALTER TABLE conversation_events ADD COLUMN user_id TEXT;
