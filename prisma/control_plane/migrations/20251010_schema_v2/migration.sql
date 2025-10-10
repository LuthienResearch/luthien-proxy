-- Drop old tables
DROP TABLE IF EXISTS conversation_tool_calls CASCADE;
DROP TABLE IF EXISTS conversation_judge_decisions CASCADE;
DROP TABLE IF EXISTS conversation_events CASCADE;
DROP TABLE IF EXISTS conversation_calls CASCADE;

-- Create new conversation_calls table (simplified)
CREATE TABLE conversation_calls (
    call_id TEXT PRIMARY KEY,
    model_name TEXT,
    provider TEXT,
    status TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX idx_conversation_calls_created ON conversation_calls(created_at);

-- Create new conversation_events table (request/response only)
CREATE TABLE conversation_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id TEXT NOT NULL REFERENCES conversation_calls(call_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_conversation_events_call_sequence ON conversation_events(call_id, sequence);
CREATE INDEX idx_conversation_events_type ON conversation_events(event_type);
CREATE INDEX idx_conversation_events_created ON conversation_events(created_at);

-- Create new policy_events table
CREATE TABLE policy_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id TEXT NOT NULL REFERENCES conversation_calls(call_id) ON DELETE CASCADE,
    policy_class TEXT NOT NULL,
    policy_config JSONB,
    event_type TEXT NOT NULL,
    original_event_id UUID REFERENCES conversation_events(id) ON DELETE SET NULL,
    modified_event_id UUID REFERENCES conversation_events(id) ON DELETE SET NULL,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_policy_events_call_created ON policy_events(call_id, created_at);
CREATE INDEX idx_policy_events_type ON policy_events(event_type);
CREATE INDEX idx_policy_events_created ON policy_events(created_at);
