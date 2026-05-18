-- ABOUTME: Adds full-text search support to conversation_events for session search API
-- ABOUTME: Creates tsvector column from user message and assistant response text content
-- ABOUTME: Uses a trigger to keep tsvector up to date on insert

-- Function to extract searchable text from a conversation event payload.
-- Targets user message text and assistant response text content only --
-- avoids indexing tool schemas, image blocks, base64 data, and metadata noise.
CREATE OR REPLACE FUNCTION _extract_event_search_text(payload JSONB) RETURNS TEXT AS $$
DECLARE
    result TEXT := '';
    msg JSONB;
    block JSONB;
BEGIN
    IF payload ? 'final_request' AND payload->'final_request' ? 'messages' THEN
        FOR msg IN SELECT * FROM jsonb_array_elements(payload->'final_request'->'messages')
        LOOP
            IF msg->>'role' = 'user' THEN
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
$$ LANGUAGE plpgsql IMMUTABLE;

ALTER TABLE conversation_events
    ADD COLUMN IF NOT EXISTS search_vector TSVECTOR;

UPDATE conversation_events
SET search_vector = to_tsvector('english', COALESCE(_extract_event_search_text(payload), ''))
WHERE event_type = 'transaction.request_recorded'
  AND _extract_event_search_text(payload) IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_events_search_vector
    ON conversation_events USING GIN (search_vector)
    WHERE search_vector IS NOT NULL;

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

DROP TRIGGER IF EXISTS trg_conversation_events_search_vector ON conversation_events;

CREATE TRIGGER trg_conversation_events_search_vector
    BEFORE INSERT ON conversation_events
    FOR EACH ROW
    EXECUTE FUNCTION _update_conversation_event_search_vector();

-- Btree prefix-match index for session_id LIKE 'prefix%' queries.
CREATE INDEX IF NOT EXISTS idx_conversation_events_session_id_btree
    ON conversation_events (session_id text_pattern_ops)
    WHERE session_id IS NOT NULL;

-- Expression index on payload->>'final_model' for model filter queries.
CREATE INDEX IF NOT EXISTS idx_conversation_events_final_model
    ON conversation_events ((payload->>'final_model'))
    WHERE event_type = 'transaction.request_recorded'
    AND payload->>'final_model' IS NOT NULL;
