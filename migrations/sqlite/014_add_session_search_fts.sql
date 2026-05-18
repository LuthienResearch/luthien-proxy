-- ABOUTME: Adds FTS5 virtual table for conversation_events full-text search.
-- ABOUTME: Mirrors the Postgres tsvector approach via SQLite's native FTS engine.
-- ABOUTME: INSERT + DELETE triggers keep FTS rows in sync with conversation_events.
--
-- Tokenizer choice: `porter` stems English words (running/runs/ran -> run), matching
-- the Postgres side which uses `plainto_tsquery('english', ...)` over a tsvector
-- built with `to_tsvector('english', ...)`. Stemming parity is required for
-- dialect-agnostic search -- the same query must return comparable results on
-- both backends.
--
-- Trigger scope matches the Postgres migration: tsvector is populated only at
-- INSERT (conversation_events are effectively immutable event records, so an
-- UPDATE trigger would be asymmetrical -- Postgres doesn't have one). A DELETE
-- trigger is needed on SQLite because the FTS5 virtual table is external to
-- conversation_events; the Postgres tsvector column is deleted automatically
-- when its row is removed via CASCADE from conversation_calls.

CREATE VIRTUAL TABLE IF NOT EXISTS conversation_events_fts USING fts5(
    session_id UNINDEXED,
    event_id UNINDEXED,
    content,
    tokenize = 'porter'
);

CREATE INDEX IF NOT EXISTS idx_conversation_events_session_id_btree
    ON conversation_events(session_id)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_events_final_model
    ON conversation_events(json_extract(payload, '$.final_model'))
    WHERE event_type = 'transaction.request_recorded';

-- Backfill existing rows. The 4-branch UNION ALL covers:
--   1. user string content
--   2. user array-of-blocks content (only type='text' blocks)
--   3. assistant string response content
--   4. assistant array-of-blocks response content
INSERT INTO conversation_events_fts(session_id, event_id, content)
SELECT session_id, event_id, content FROM (
    SELECT
        ce.session_id AS session_id,
        ce.id AS event_id,
        TRIM(COALESCE((
            SELECT group_concat(t, ' ') FROM (
                SELECT json_extract(msg.value, '$.content') AS t
                FROM json_each(COALESCE(json_extract(ce.payload, '$.final_request.messages'), '[]')) AS msg
                WHERE json_extract(msg.value, '$.role') = 'user'
                  AND json_type(msg.value, '$.content') = 'text'
                UNION ALL
                SELECT json_extract(block.value, '$.text') AS t
                FROM json_each(COALESCE(json_extract(ce.payload, '$.final_request.messages'), '[]')) AS msg,
                     json_each(COALESCE(json_extract(msg.value, '$.content'), '[]')) AS block
                WHERE json_extract(msg.value, '$.role') = 'user'
                  AND json_type(msg.value, '$.content') = 'array'
                  AND json_extract(block.value, '$.type') = 'text'
                UNION ALL
                SELECT json_extract(ce.payload, '$.final_response.content') AS t
                WHERE json_type(ce.payload, '$.final_response.content') = 'text'
                UNION ALL
                SELECT json_extract(block.value, '$.text') AS t
                FROM json_each(COALESCE(json_extract(ce.payload, '$.final_response.content'), '[]')) AS block
                WHERE json_type(ce.payload, '$.final_response.content') = 'array'
                  AND json_extract(block.value, '$.type') = 'text'
            ) WHERE t IS NOT NULL AND t != ''
        ), '')) AS content
    FROM conversation_events ce
    WHERE ce.event_type = 'transaction.request_recorded'
) WHERE content != '';

CREATE TRIGGER IF NOT EXISTS trg_conversation_events_fts_insert
AFTER INSERT ON conversation_events
WHEN NEW.event_type = 'transaction.request_recorded'
BEGIN
    INSERT INTO conversation_events_fts(session_id, event_id, content)
    SELECT NEW.session_id, NEW.id, content FROM (
        SELECT TRIM(COALESCE((
            SELECT group_concat(t, ' ') FROM (
                SELECT json_extract(msg.value, '$.content') AS t
                FROM json_each(COALESCE(json_extract(NEW.payload, '$.final_request.messages'), '[]')) AS msg
                WHERE json_extract(msg.value, '$.role') = 'user'
                  AND json_type(msg.value, '$.content') = 'text'
                UNION ALL
                SELECT json_extract(block.value, '$.text') AS t
                FROM json_each(COALESCE(json_extract(NEW.payload, '$.final_request.messages'), '[]')) AS msg,
                     json_each(COALESCE(json_extract(msg.value, '$.content'), '[]')) AS block
                WHERE json_extract(msg.value, '$.role') = 'user'
                  AND json_type(msg.value, '$.content') = 'array'
                  AND json_extract(block.value, '$.type') = 'text'
                UNION ALL
                SELECT json_extract(NEW.payload, '$.final_response.content') AS t
                WHERE json_type(NEW.payload, '$.final_response.content') = 'text'
                UNION ALL
                SELECT json_extract(block.value, '$.text') AS t
                FROM json_each(COALESCE(json_extract(NEW.payload, '$.final_response.content'), '[]')) AS block
                WHERE json_type(NEW.payload, '$.final_response.content') = 'array'
                  AND json_extract(block.value, '$.type') = 'text'
            ) WHERE t IS NOT NULL AND t != ''
        ), '')) AS content
    ) WHERE content != '';
END;

-- Remove the FTS row whenever a conversation_events row is deleted. This
-- includes CASCADE deletes triggered by dropping a conversation_calls parent.
-- Without this trigger the FTS table would accumulate orphan rows that still
-- surface in MATCH queries.
CREATE TRIGGER IF NOT EXISTS trg_conversation_events_fts_delete
AFTER DELETE ON conversation_events
BEGIN
    DELETE FROM conversation_events_fts WHERE event_id = OLD.id;
END;
