CREATE TABLE IF NOT EXISTS conversation_calls (
    call_id TEXT PRIMARY KEY,
    trace_id TEXT NULL,
    model_name TEXT NULL,
    provider TEXT NULL,
    status TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    metadata JSONB NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_calls_trace ON conversation_calls (trace_id);
CREATE INDEX IF NOT EXISTS idx_conversation_calls_created ON conversation_calls (created_at);

CREATE TABLE IF NOT EXISTS conversation_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id TEXT NOT NULL REFERENCES conversation_calls(call_id) ON DELETE CASCADE,
    trace_id TEXT NULL,
    event_type TEXT NOT NULL,
    hook TEXT NOT NULL,
    sequence BIGINT NULL,
    chunk_index INT NULL,
    choice_index INT NULL,
    role TEXT NULL,
    delta_text TEXT NULL,
    raw_chunk JSONB NULL,
    payload JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_events_call_created ON conversation_events (call_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_events_trace_created ON conversation_events (trace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_events_call_sequence ON conversation_events (call_id, sequence);
CREATE INDEX IF NOT EXISTS idx_conversation_events_type ON conversation_events (event_type);

CREATE TABLE IF NOT EXISTS conversation_tool_calls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id TEXT NOT NULL REFERENCES conversation_calls(call_id) ON DELETE CASCADE,
    trace_id TEXT NULL,
    tool_call_id TEXT NULL,
    name TEXT NULL,
    arguments JSONB NULL,
    status TEXT NULL,
    response JSONB NULL,
    chunks_buffered INT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_tool_calls_call_created ON conversation_tool_calls (call_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_tool_calls_tool_id ON conversation_tool_calls (tool_call_id);
CREATE INDEX IF NOT EXISTS idx_conversation_tool_calls_trace_created ON conversation_tool_calls (trace_id, created_at);
ALTER TABLE conversation_tool_calls
    ADD CONSTRAINT uq_conversation_tool_calls_call_tool UNIQUE (call_id, tool_call_id);
