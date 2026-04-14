-- ABOUTME: Adds FTS5 virtual table for conversation_events full-text search.
-- ABOUTME: Mirrors the Postgres tsvector approach via SQLite's native FTS engine.
-- ABOUTME: A trigger keeps the FTS table in sync on insert; existing rows are backfilled.

CREATE VIRTUAL TABLE IF NOT EXISTS conversation_events_fts USING fts5(
    session_id UNINDEXED,
    event_id UNINDEXED,
    content
);

CREATE INDEX IF NOT EXISTS idx_conversation_events_session_id_btree
    ON conversation_events(session_id)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_events_final_model
    ON conversation_events(json_extract(payload, '$.final_model'))
    WHERE event_type = 'transaction.request_recorded';

-- Backfill existing rows. Only insert rows that produce non-empty content.
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
