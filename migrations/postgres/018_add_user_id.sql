-- ABOUTME: Adds user_id column to conversation_calls
-- ABOUTME: Enables tracking which user made each request for team deployments
-- ABOUTME: Extracted from X-Luthien-User-Id header or JWT Bearer token sub claim

-- user_id on conversation_calls (one row per API call). This is the column the
-- history API filters/joins against; it gets a partial index for fast lookup.
ALTER TABLE conversation_calls ADD COLUMN IF NOT EXISTS user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_conversation_calls_user ON conversation_calls(user_id) WHERE user_id IS NOT NULL;

-- Renumbered from 014 due to collision with main; see PR #743.
