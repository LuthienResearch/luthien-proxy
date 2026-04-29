-- ABOUTME: Adds full-text search support to conversation_events for session search API
-- ABOUTME: Creates tsvector column from user message and assistant response text content
-- ABOUTME: Uses a trigger to keep tsvector up to date on insert

-- Function to extract searchable text from a conversation event payload.
-- Targets user message text and assistant response text content only —
-- avoids indexing tool schemas, image blocks, base64 data, and metadata noise.
CREATE OR REPLACE FUNCTION _extract_event_search_text(payload JSONB) RETURNS TEXT AS $$
DECLARE
    result TEXT := '';
    msg JSONB;
    block JSONB;
    msg_text TEXT;
BEGIN
    -- Extract text from final_request messages (user turns)
    IF payload ? 'final_request' AND payload->'final_request' ? 'messages' THEN
        FOR msg IN SELECT * FROM jsonb_array_elements(payload->'final_request'->'messages')
        LOOP
            IF msg->>'role' = 'user' THEN
                -- Content may be a string or array of content blocks
                IF jsonb_typeof(msg->'content') = 'string' THEN
                    result := result || ' ' || (msg->>'content');
                ELSIF jsonb_typeof(msg->'content') = 'array' THEN
                    FOR block IN SELECT * FROM jsonb_array_elements(msg->'content')
                    LOOP
                        IF block->>'type' = 'text' THEN
                            result := result || ' ' || (block->>'text');
                        END IF;
                    END LOOP;
                END IF;
            END IF;
        END LOOP;
    END IF;

    -- Extract text from final_response content (assistant turns)
    IF payload ? 'final_response' AND payload->'final_response' ? 'content' THEN
        IF jsonb_typeof(payload->'final_response'->'content') = 'string' THEN
            result := result || ' ' || (payload->'final_response'->>'content');
        ELSIF jsonb_typeof(payload->'final_response'->'content') = 'array' THEN
            FOR block IN SELECT * FROM jsonb_array_elements(payload->'final_response'->'content')
            LOOP
                IF block->>'type' = 'text' THEN
                    result := result || ' ' || (block->>'text');
                END IF;
            END LOOP;
        END IF;
    END IF;

    RETURN NULLIF(TRIM(result), '');
END;
$$ LANGUAGE plpgsql STABLE;

-- Add tsvector column to conversation_events
ALTER TABLE conversation_events
    ADD COLUMN IF NOT EXISTS search_vector TSVECTOR;

-- Backfill existing rows for transaction.request_recorded events
-- (these are the events that contain final_request/final_response payloads)
-- CTE ensures the search-text extraction function is called exactly once per row.
-- NOTE: This backfill runs as a single unbounded UPDATE. On large production databases
-- (millions of rows), this may hold row locks for an extended period and generate a
-- large WAL segment. Consider running manually in batches if the table is large.
WITH computed AS (
    SELECT id, _extract_event_search_text(payload) AS search_text
    FROM conversation_events
    WHERE event_type = 'transaction.request_recorded'
)
UPDATE conversation_events ce
SET search_vector = to_tsvector('english', COALESCE(c.search_text, ''))
FROM computed c
WHERE ce.id = c.id
  AND c.search_text IS NOT NULL;

-- GIN index for fast tsvector queries
-- CONCURRENTLY: run-migrations.sh runs psql -f without BEGIN/COMMIT wrapping,
-- so each statement runs in autocommit mode — CREATE INDEX CONCURRENTLY is safe.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversation_events_search_vector
    ON conversation_events USING GIN (search_vector)
    WHERE search_vector IS NOT NULL;

-- Trigger function to auto-populate search_vector on insert
CREATE OR REPLACE FUNCTION _update_conversation_event_search_vector() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.event_type = 'transaction.request_recorded' THEN
        NEW.search_vector := to_tsvector(
            'english',
            COALESCE(_extract_event_search_text(NEW.payload), '')
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop trigger if it exists (idempotent)
DROP TRIGGER IF EXISTS trg_conversation_events_search_vector ON conversation_events;

-- Create trigger on insert only (updates are rare; backfill handles existing rows)
CREATE TRIGGER trg_conversation_events_search_vector
    BEFORE INSERT ON conversation_events
    FOR EACH ROW
    EXECUTE FUNCTION _update_conversation_event_search_vector();

-- Index on session_id for prefix-match user filter (btree, already exists from 006 but
-- that one is a partial index; add a full btree for prefix queries via LIKE 'prefix%')
CREATE INDEX IF NOT EXISTS idx_conversation_events_session_id_btree
    ON conversation_events (session_id text_pattern_ops)
    WHERE session_id IS NOT NULL;

-- Index on conversation_events.created_at is already present from 003.
-- Expression index on payload->>'final_model' for model filter queries.
-- The service layer filters models via JSONB extraction on conversation_events,
-- not via conversation_calls.model_name, so we index the expression used in queries.
CREATE INDEX IF NOT EXISTS idx_conversation_events_final_model
    ON conversation_events ((payload->>'final_model'))
    WHERE event_type = 'transaction.request_recorded'
    AND payload->>'final_model' IS NOT NULL;
