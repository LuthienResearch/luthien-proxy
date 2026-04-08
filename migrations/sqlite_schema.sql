-- SQLite schema representing the final state of all PostgreSQL migrations.
-- Applied automatically when using SQLite (no Docker migration runner needed).
--
-- NOTE: A bundled copy lives at src/luthien_proxy/utils/sqlite_schema.sql
-- for pip-installed environments. When updating this file, copy it there too.

-- Migration tracking (matches the PostgreSQL _migrations table)
CREATE TABLE IF NOT EXISTS _migrations (
    filename TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    content_hash TEXT
);

-- Current policy (single-row, from migration 005)
CREATE TABLE IF NOT EXISTS current_policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    policy_class_ref TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enabled_at TEXT NOT NULL DEFAULT (datetime('now')),
    enabled_by TEXT
);

-- Debug logs
CREATE TABLE IF NOT EXISTS debug_logs (
    id TEXT PRIMARY KEY,
    time_created TEXT NOT NULL DEFAULT (datetime('now')),
    debug_type_identifier TEXT NOT NULL,
    jsonblob TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_debug_logs_created_at ON debug_logs (time_created DESC);
CREATE INDEX IF NOT EXISTS idx_debug_logs_type ON debug_logs (debug_type_identifier);

-- Conversation calls (one row per API call)
CREATE TABLE IF NOT EXISTS conversation_calls (
    call_id TEXT PRIMARY KEY,
    model_name TEXT,
    provider TEXT,
    status TEXT,
    session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_conversation_calls_created ON conversation_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_calls_session ON conversation_calls(session_id);

-- Conversation events (request/response events per call)
CREATE TABLE IF NOT EXISTS conversation_events (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES conversation_calls(call_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_conversation_events_call_created ON conversation_events(call_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_events_type ON conversation_events(event_type);
CREATE INDEX IF NOT EXISTS idx_conversation_events_created ON conversation_events(created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_events_session ON conversation_events(session_id);

-- Policy events
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

-- Judge decisions
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

-- Auth config (single-row)
CREATE TABLE IF NOT EXISTS auth_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    auth_mode TEXT NOT NULL DEFAULT 'both',
    validate_credentials INTEGER NOT NULL DEFAULT 1,
    valid_cache_ttl_seconds INTEGER NOT NULL DEFAULT 3600,
    invalid_cache_ttl_seconds INTEGER NOT NULL DEFAULT 300,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by TEXT
);
INSERT OR IGNORE INTO auth_config (id) VALUES (1);

-- Request logs
CREATE TABLE IF NOT EXISTS request_logs (
    id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    session_id TEXT,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    http_method TEXT,
    url TEXT,
    request_headers TEXT,
    request_body TEXT,
    response_status INTEGER,
    response_headers TEXT,
    response_body TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    duration_ms REAL,
    model TEXT,
    is_streaming INTEGER DEFAULT 0,
    endpoint TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_request_logs_transaction_id ON request_logs(transaction_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_session_id ON request_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_started_at ON request_logs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_request_logs_direction ON request_logs(direction);
CREATE INDEX IF NOT EXISTS idx_request_logs_endpoint ON request_logs(endpoint);
CREATE INDEX IF NOT EXISTS idx_request_logs_response_status ON request_logs(response_status);
CREATE INDEX IF NOT EXISTS idx_request_logs_model ON request_logs(model);

-- Telemetry config (single-row)
CREATE TABLE IF NOT EXISTS telemetry_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER,
    deployment_id TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT DEFAULT (datetime('now')),
    updated_by TEXT
);
INSERT OR IGNORE INTO telemetry_config (id) VALUES (1);

-- Session rules (extracted per-session, e.g. from CLAUDE.md)
CREATE TABLE IF NOT EXISTS session_rules (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    rule_instruction TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_session_rules_session ON session_rules(session_id);
