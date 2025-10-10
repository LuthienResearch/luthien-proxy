CREATE TABLE IF NOT EXISTS conversation_judge_decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id TEXT NOT NULL,
    trace_id TEXT NULL,
    tool_call_id TEXT NULL,
    probability DOUBLE PRECISION NULL,
    explanation TEXT NULL,
    tool_call JSONB NULL,
    judge_prompt JSONB NULL,
    judge_response_text TEXT NULL,
    original_request JSONB NULL,
    original_response JSONB NULL,
    stream_chunks JSONB NULL,
    blocked_response JSONB NULL,
    timing JSONB NULL,
    judge_config JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_judge_decisions_trace_created
    ON conversation_judge_decisions (trace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_judge_decisions_call_created
    ON conversation_judge_decisions (call_id, created_at);

CREATE INDEX IF NOT EXISTS idx_judge_decisions_created
    ON conversation_judge_decisions (created_at);
