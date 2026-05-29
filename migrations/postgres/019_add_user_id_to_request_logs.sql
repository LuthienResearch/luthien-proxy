-- ABOUTME: Adds user_id column to request_logs
-- ABOUTME: Mirrors the user_id already on conversation_calls so HTTP-level
-- ABOUTME: logs can be filtered/attributed per user on shared deployments.

ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_request_logs_user ON request_logs(user_id) WHERE user_id IS NOT NULL;
