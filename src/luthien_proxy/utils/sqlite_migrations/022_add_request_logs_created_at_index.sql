-- ABOUTME: Adds an index on request_logs.created_at
-- ABOUTME: Mirrors the Postgres migration; SQLite uses a plain index statement
-- ABOUTME: for the dockerless single-user database path.
CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON request_logs (created_at);
