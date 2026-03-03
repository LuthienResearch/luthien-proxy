-- Request/response logging table for HTTP-level debugging.
-- Captures both inbound (client↔proxy) and outbound (proxy↔backend)
-- HTTP details with structured, queryable columns.

\c luthien_control;

CREATE TABLE IF NOT EXISTS request_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Links both rows (inbound + outbound) for a single proxy call
    transaction_id TEXT NOT NULL,
    session_id TEXT,

    -- 'inbound' = client↔proxy, 'outbound' = proxy↔backend
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),

    -- HTTP request
    http_method TEXT,
    url TEXT,
    request_headers JSONB,
    request_body JSONB,

    -- HTTP response
    response_status INTEGER,
    response_headers JSONB,
    response_body JSONB,

    -- Timing
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    duration_ms DOUBLE PRECISION,

    -- Metadata
    model TEXT,
    is_streaming BOOLEAN DEFAULT FALSE,
    endpoint TEXT,
    error TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- TODO: Add a retention policy (e.g. scheduled job or pg_partman) to prevent
-- unbounded table growth. Consider 30-90 day retention based on usage patterns.

-- Indexes for common query patterns
CREATE INDEX idx_request_logs_transaction_id ON request_logs(transaction_id);
CREATE INDEX idx_request_logs_session_id ON request_logs(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX idx_request_logs_started_at ON request_logs(started_at DESC);
CREATE INDEX idx_request_logs_direction ON request_logs(direction);
CREATE INDEX idx_request_logs_endpoint ON request_logs(endpoint);
CREATE INDEX idx_request_logs_response_status ON request_logs(response_status);
CREATE INDEX idx_request_logs_model ON request_logs(model);
CREATE INDEX idx_request_logs_direction_started_at ON request_logs(direction, started_at DESC);

GRANT ALL PRIVILEGES ON TABLE request_logs TO luthien;
