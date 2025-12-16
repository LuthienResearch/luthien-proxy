-- ABOUTME: Creates conversation_transcript view for human-readable prompt/response logs
-- ABOUTME: Extracts clean text from conversation_events JSON payloads

-- =============================================================================
-- conversation_transcript View
-- =============================================================================
-- PURPOSE: Provides a human-readable view of conversations for debugging.
-- Instead of digging through raw JSON in conversation_events, this view
-- extracts the actual prompt/response text.
--
-- WHY THIS MATTERS: During dogfooding, Scott needed to debug an image issue
-- and found the raw conversation_events table too messy. This view gives a
-- clean CSV-exportable format for reviewing what actually happened.
--
-- LIMITATIONS (see docs/database-schema.md for full details):
-- - Only extracts the LAST user message from multi-message requests
-- - Tool calls are not shown (text content only)
-- - Response extraction assumes OpenAI format (choices->message->content)
-- - session_id may be NULL for some events
-- =============================================================================

CREATE OR REPLACE VIEW conversation_transcript AS
SELECT
    ce.session_id,
    ce.created_at,

    -- Map event types to human-readable PROMPT/RESPONSE labels
    CASE
        WHEN ce.event_type = 'pipeline.client_request' THEN 'PROMPT'
        WHEN ce.event_type LIKE '%response_recorded' THEN 'RESPONSE'
    END AS prompt_or_response,

    -- Extract model name from the appropriate JSON path per event type
    CASE
        WHEN ce.event_type = 'pipeline.client_request' THEN
            ce.payload->'payload'->>'model'
        WHEN ce.event_type LIKE '%response_recorded' THEN
            ce.payload->'final_response'->>'model'
    END AS model,

    -- Extract content, handling format differences:
    -- - PROMPTS can be string (OpenAI) or array of content blocks (Anthropic)
    -- - RESPONSES use OpenAI format with choices[0].message.content
    -- NOTE: We only extract the LAST message (->-1) because that's the user's
    -- actual prompt in a multi-turn conversation. Earlier messages are context.
    CASE
        WHEN ce.event_type = 'pipeline.client_request' THEN
            CASE
                -- Simple string content (OpenAI format)
                WHEN jsonb_typeof(ce.payload->'payload'->'messages'->-1->'content') = 'string' THEN
                    ce.payload->'payload'->'messages'->-1->>'content'
                -- Array of content blocks (Anthropic format) - join all text blocks
                WHEN jsonb_typeof(ce.payload->'payload'->'messages'->-1->'content') = 'array' THEN
                    (SELECT string_agg(elem->>'text', ' ')
                     FROM jsonb_array_elements(ce.payload->'payload'->'messages'->-1->'content') AS elem
                     WHERE elem->>'type' = 'text')
                ELSE NULL
            END
        WHEN ce.event_type LIKE '%response_recorded' THEN
            -- Response content is in OpenAI format (LiteLLM standardizes to this)
            ce.payload->'final_response'->'choices'->0->'message'->>'content'
    END AS content,

    -- Always 'Y' for Luthien-logged data. This column exists so users can
    -- combine Luthien logs with manually-added entries and distinguish them.
    'Y' AS logged_by_luthien,

    ce.call_id

FROM conversation_events ce
WHERE ce.event_type IN (
    'pipeline.client_request',                    -- User prompts
    'transaction.streaming_response_recorded',    -- Streaming responses
    'transaction.non_streaming_response_recorded' -- Non-streaming responses
);

COMMENT ON VIEW conversation_transcript IS
    'Human-readable conversation log. Use for debugging instead of raw conversation_events. '
    'No ORDER BY - add your own when querying. See docs/database-schema.md for limitations.';

-- Performance index for filtering by event_type and ordering by time
CREATE INDEX IF NOT EXISTS idx_conversation_events_type_created
ON conversation_events(event_type, created_at);
