-- ABOUTME: Adds user_id column to request_logs
-- ABOUTME: Mirrors the user_id already on conversation_calls so HTTP-level
-- ABOUTME: logs can be filtered/attributed per user on shared deployments.

-- No IF NOT EXISTS on ALTER TABLE ADD COLUMN — the migration runner guarantees
-- each migration runs exactly once, so idempotency is handled by the tracker.
ALTER TABLE request_logs ADD COLUMN user_id TEXT;
-- Partial index (WHERE user_id IS NOT NULL) to match the Postgres migration:
-- NULL rows aren't indexed (most rows when attribution is off), keeping the
-- index small. SQLite 3.8+ supports partial indexes.
CREATE INDEX IF NOT EXISTS idx_request_logs_user ON request_logs(user_id) WHERE user_id IS NOT NULL;
