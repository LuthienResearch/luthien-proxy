-- Add index on conversation_calls.created_at for retention purge performance.
-- The ConversationPurger queries this column with a WHERE created_at < $1 clause.
CREATE INDEX IF NOT EXISTS idx_conversation_calls_created_at
    ON conversation_calls(created_at DESC);
