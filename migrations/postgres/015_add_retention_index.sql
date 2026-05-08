-- Add index on conversation_calls.created_at for retention purge performance.
-- The ConversationPurger queries this column with a WHERE created_at < $1 clause.
--
-- CONCURRENTLY: avoid taking a SHARE lock on conversation_calls during the
-- index build. conversation_calls is on the gateway request hot path; a
-- non-concurrent build would stall every write for the duration. The
-- migration runner (docker/run-migrations.sh) invokes `psql -f`, which runs
-- each statement in autocommit, so CONCURRENTLY is allowed here. If the build
-- fails partway, an INVALID index will be left behind; drop it manually
-- before retrying the migration.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversation_calls_created_at
    ON conversation_calls(created_at DESC);
