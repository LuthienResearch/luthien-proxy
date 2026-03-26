-- ABOUTME: Request/response logging table for HTTP-level debugging
-- ABOUTME: Captures inbound and outbound HTTP details

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
CREATE INDEX IF NOT EXISTS idx_request_logs_direction_started_at ON request_logs(direction, started_at DESC);
