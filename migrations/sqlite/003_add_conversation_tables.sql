-- ABOUTME: Creates conversation tracking tables for debug/trace functionality
-- ABOUTME: Includes conversation_calls, conversation_events, policy_events, and judge_decisions

CREATE TABLE IF NOT EXISTS debug_logs (
    id TEXT PRIMARY KEY,
    time_created TEXT NOT NULL DEFAULT (datetime('now')),
    debug_type_identifier TEXT NOT NULL,
    jsonblob TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_debug_logs_created_at ON debug_logs (time_created DESC);
CREATE INDEX IF NOT EXISTS idx_debug_logs_type ON debug_logs (debug_type_identifier);

CREATE TABLE IF NOT EXISTS conversation_calls (
    call_id TEXT PRIMARY KEY,
    model_name TEXT,
    provider TEXT,
    status TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_conversation_calls_created ON conversation_calls(created_at);

CREATE TABLE IF NOT EXISTS conversation_events (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES conversation_calls(call_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_conversation_events_call_sequence ON conversation_events(call_id, sequence);
CREATE INDEX IF NOT EXISTS idx_conversation_events_type ON conversation_events(event_type);
CREATE INDEX IF NOT EXISTS idx_conversation_events_created ON conversation_events(created_at);

CREATE TABLE IF NOT EXISTS policy_events (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES conversation_calls(call_id) ON DELETE CASCADE,
    policy_class TEXT NOT NULL,
    policy_config TEXT,
    event_type TEXT NOT NULL,
    original_event_id TEXT REFERENCES conversation_events(id) ON DELETE SET NULL,
    modified_event_id TEXT REFERENCES conversation_events(id) ON DELETE SET NULL,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_policy_events_call_created ON policy_events(call_id, created_at);
CREATE INDEX IF NOT EXISTS idx_policy_events_type ON policy_events(event_type);
CREATE INDEX IF NOT EXISTS idx_policy_events_created ON policy_events(created_at);

CREATE TABLE IF NOT EXISTS conversation_judge_decisions (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES conversation_calls(call_id) ON DELETE CASCADE,
    trace_id TEXT,
    tool_call_id TEXT,
    probability REAL,
    explanation TEXT,
    tool_call TEXT,
    judge_prompt TEXT,
    judge_response_text TEXT,
    original_request TEXT,
    original_response TEXT,
    stream_chunks TEXT,
    blocked_response TEXT,
    timing TEXT,
    judge_config TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_judge_decisions_trace_created ON conversation_judge_decisions (trace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_judge_decisions_call_created ON conversation_judge_decisions (call_id, created_at);
CREATE INDEX IF NOT EXISTS idx_judge_decisions_created ON conversation_judge_decisions (created_at);
