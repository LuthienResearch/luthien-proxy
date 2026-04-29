-- ABOUTME: Extends session search tsvector coverage to include response events
-- ABOUTME: Migration 016 only indexed transaction.request_recorded events, but
-- ABOUTME: final_response is stored in transaction.{streaming,non_streaming}_response_recorded

-- Update trigger function to also handle response events
CREATE OR REPLACE FUNCTION _update_conversation_event_search_vector() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.event_type IN (
        'transaction.request_recorded',
        'transaction.streaming_response_recorded',
        'transaction.non_streaming_response_recorded'
    ) THEN
        NEW.search_vector := to_tsvector(
            'english',
            COALESCE(_extract_event_search_text(NEW.payload), '')
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Backfill existing response events
-- CTE ensures the search-text extraction function is called exactly once per row.
WITH computed AS (
    SELECT id, _extract_event_search_text(payload) AS search_text
    FROM conversation_events
    WHERE event_type IN (
        'transaction.streaming_response_recorded',
        'transaction.non_streaming_response_recorded'
    )
    AND search_vector IS NULL
)
UPDATE conversation_events ce
SET search_vector = to_tsvector('english', COALESCE(c.search_text, ''))
FROM computed c
WHERE ce.id = c.id
  AND c.search_text IS NOT NULL;
