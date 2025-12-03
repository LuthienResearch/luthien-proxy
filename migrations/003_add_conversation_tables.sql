-- ABOUTME: Creates conversation tracking tables for debug/trace functionality
-- ABOUTME: Includes conversation_calls, conversation_events, policy_events, and judge_decisions

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Debug logs table (for general debugging)
CREATE TABLE IF NOT EXISTS debug_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    time_created TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    debug_type_identifier TEXT NOT NULL,
    jsonblob JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_debug_logs_created_at ON debug_logs (time_created DESC);
CREATE INDEX IF NOT EXISTS idx_debug_logs_type ON debug_logs (debug_type_identifier);

-- Conversation calls table (one row per API call)
CREATE TABLE IF NOT EXISTS conversation_calls (
    call_id TEXT PRIMARY KEY,
    model_name TEXT,
    provider TEXT,
    status TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversation_calls_created ON conversation_calls(created_at);

-- Conversation events table (request/response events per call)
CREATE TABLE IF NOT EXISTS conversation_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id TEXT NOT NULL REFERENCES conversation_calls(call_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_events_call_sequence ON conversation_events(call_id, sequence);
CREATE INDEX IF NOT EXISTS idx_conversation_events_type ON conversation_events(event_type);
CREATE INDEX IF NOT EXISTS idx_conversation_events_created ON conversation_events(created_at);

-- Policy events table (policy decisions and modifications)
CREATE TABLE IF NOT EXISTS policy_events (
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

CREATE INDEX IF NOT EXISTS idx_policy_events_call_created ON policy_events(call_id, created_at);
CREATE INDEX IF NOT EXISTS idx_policy_events_type ON policy_events(event_type);
CREATE INDEX IF NOT EXISTS idx_policy_events_created ON policy_events(created_at);

-- Judge decisions table (for LLM judge policies)
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

CREATE INDEX IF NOT EXISTS idx_judge_decisions_trace_created ON conversation_judge_decisions (trace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_judge_decisions_call_created ON conversation_judge_decisions (call_id, created_at);
CREATE INDEX IF NOT EXISTS idx_judge_decisions_created ON conversation_judge_decisions (created_at);
