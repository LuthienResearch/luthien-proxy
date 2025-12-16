-- ABOUTME: Creates conversation_transcript view for human-readable prompt/response logs
-- ABOUTME: Extracts clean text from conversation_events JSON payloads

-- Create view for human-readable conversation transcripts
CREATE OR REPLACE VIEW conversation_transcript AS
SELECT
    ce.session_id,
    ce.created_at,
    CASE
        WHEN ce.event_type = 'pipeline.client_request' THEN 'PROMPT'
        WHEN ce.event_type LIKE '%response_recorded' THEN 'RESPONSE'
    END AS prompt_or_response,
    -- Extract model from appropriate location based on event type
    CASE
        WHEN ce.event_type = 'pipeline.client_request' THEN
            ce.payload->'payload'->>'model'
        WHEN ce.event_type LIKE '%response_recorded' THEN
            ce.payload->'final_response'->>'model'
    END AS model,
    -- Extract content, handling both string and array (Anthropic) formats
    CASE
        WHEN ce.event_type = 'pipeline.client_request' THEN
            CASE
                -- If content is a string, use it directly
                WHEN jsonb_typeof(ce.payload->'payload'->'messages'->-1->'content') = 'string' THEN
                    ce.payload->'payload'->'messages'->-1->>'content'
                -- If content is an array (Anthropic format), extract text from first text block
                WHEN jsonb_typeof(ce.payload->'payload'->'messages'->-1->'content') = 'array' THEN
                    (SELECT string_agg(elem->>'text', ' ')
                     FROM jsonb_array_elements(ce.payload->'payload'->'messages'->-1->'content') AS elem
                     WHERE elem->>'type' = 'text')
                ELSE NULL
            END
        WHEN ce.event_type LIKE '%response_recorded' THEN
            ce.payload->'final_response'->'choices'->0->'message'->>'content'
    END AS content,
    'Y' AS logged_by_luthien,
    ce.call_id
FROM conversation_events ce
WHERE ce.event_type IN (
    'pipeline.client_request',
    'transaction.streaming_response_recorded',
    'transaction.non_streaming_response_recorded'
)
ORDER BY ce.created_at;

-- Add comment describing the view
COMMENT ON VIEW conversation_transcript IS 'Human-readable conversation log with clean prompt/response format. Use this for debugging and session review instead of raw conversation_events.';
