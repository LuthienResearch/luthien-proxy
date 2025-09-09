-- ABOUTME: Creates request_logs table for storing all LLM requests and responses
-- ABOUTME: Enables MVP logging functionality for viewing recent API calls

-- Table for logging all requests and responses
CREATE TABLE IF NOT EXISTS request_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    episode_id UUID REFERENCES episodes(id),
    step_id UUID,
    call_type VARCHAR(50),
    stage VARCHAR(50) NOT NULL, -- 'pre', 'post', 'streaming_chunk'
    request JSONB NOT NULL,
    response JSONB,
    user_metadata JSONB DEFAULT '{}',
    policy_action VARCHAR(100), -- 'allow', 'reject', 'rewrite', 'replace_response'
    policy_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON request_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_request_logs_episode_id ON request_logs(episode_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_stage ON request_logs(stage);

-- Grant permissions
GRANT ALL PRIVILEGES ON request_logs TO luthien;
GRANT ALL PRIVILEGES ON request_logs TO litellm;
