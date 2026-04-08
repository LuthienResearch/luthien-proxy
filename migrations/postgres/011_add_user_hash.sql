-- Add user_hash column to conversation_calls and request_logs
-- for multi-user identification on shared proxy deployments.

ALTER TABLE conversation_calls ADD COLUMN IF NOT EXISTS user_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_conversation_calls_user_hash
    ON conversation_calls(user_hash) WHERE user_hash IS NOT NULL;

ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS user_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_request_logs_user_hash
    ON request_logs(user_hash) WHERE user_hash IS NOT NULL;
