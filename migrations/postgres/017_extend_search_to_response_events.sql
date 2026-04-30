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
UPDATE conversation_events
SET search_vector = to_tsvector('english', COALESCE(_extract_event_search_text(payload), ''))
WHERE event_type IN (
    'transaction.streaming_response_recorded',
    'transaction.non_streaming_response_recorded'
)
  AND _extract_event_search_text(payload) IS NOT NULL
  AND search_vector IS NULL;
