-- Initial schema for Luthien Control
-- Creates tables for policies, episodes, decisions, and audit logs

-- Extension for UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Table for storing policy configurations
CREATE TABLE IF NOT EXISTS policies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL UNIQUE,
    version VARCHAR(50) NOT NULL,
    config JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    active BOOLEAN DEFAULT true
);

-- Table for tracking episodes (conversation sessions)
CREATE TABLE IF NOT EXISTS episodes (
    id UUID PRIMARY KEY,
    user_id VARCHAR(255),
    team_id VARCHAR(255),
    policy_id UUID REFERENCES policies(id),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    ended_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(50) DEFAULT 'active'
);

-- Table for logging control decisions
CREATE TABLE IF NOT EXISTS control_decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    episode_id UUID REFERENCES episodes(id),
    step_id UUID,
    decision_type VARCHAR(100) NOT NULL,
    score DECIMAL(5,4),
    threshold DECIMAL(5,4),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Table for audit requests
CREATE TABLE IF NOT EXISTS audit_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    episode_id UUID REFERENCES episodes(id),
    step_id UUID,
    reason VARCHAR(255) NOT NULL,
    score DECIMAL(5,4),
    metadata JSONB DEFAULT '{}',
    status VARCHAR(50) DEFAULT 'pending',
    assigned_to VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    reviewed_at TIMESTAMP WITH TIME ZONE,
    review_decision VARCHAR(100),
    review_notes TEXT
);

-- Table for honeypot validation tracking
CREATE TABLE IF NOT EXISTS honeypot_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    monitor_type VARCHAR(50) NOT NULL, -- 'trusted' or 'untrusted'
    honeypot_id VARCHAR(255) NOT NULL,
    expected_violation BOOLEAN NOT NULL,
    detected_violation BOOLEAN NOT NULL,
    score DECIMAL(5,4),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Table for monitoring untrusted monitor health
CREATE TABLE IF NOT EXISTS monitor_health (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    monitor_type VARCHAR(50) NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_value DECIMAL(10,6) NOT NULL,
    threshold DECIMAL(10,6),
    status VARCHAR(50) DEFAULT 'healthy',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_episodes_user_id ON episodes(user_id);
CREATE INDEX IF NOT EXISTS idx_episodes_created_at ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_control_decisions_episode_id ON control_decisions(episode_id);
CREATE INDEX IF NOT EXISTS idx_control_decisions_created_at ON control_decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_requests_status ON audit_requests(status);
CREATE INDEX IF NOT EXISTS idx_audit_requests_created_at ON audit_requests(created_at);
CREATE INDEX IF NOT EXISTS idx_honeypot_results_monitor_type ON honeypot_results(monitor_type);
CREATE INDEX IF NOT EXISTS idx_monitor_health_monitor_type ON monitor_health(monitor_type);

-- Insert default policy
INSERT INTO policies (name, version, config) VALUES (
    'default',
    '0.1.0',
    '{
        "defer_threshold_T": 0.7,
        "streaming": {
            "max_chars_before_check": 400,
            "halt_threshold": 0.9
        },
        "resample": {
            "enabled": true,
            "k": 3,
            "trigger_threshold": 0.6,
            "audit_if_min_score_gt": 0.85
        },
        "untrusted_monitor": {
            "enabled": false,
            "honeypots": {
                "upfront_fraction": 0.15,
                "shutdown_threshold_fnr": 0.2
            },
            "paraphrase": "trusted"
        },
        "alerts": {
            "on_shutdown": ["slack:#ai-incidents"],
            "on_high_risk": ["pagerduty:ai-ops"]
        }
    }'::jsonb
) ON CONFLICT (name) DO NOTHING;

-- Create a function to update the updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create trigger to automatically update updated_at
CREATE TRIGGER update_policies_updated_at
    BEFORE UPDATE ON policies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Grant permissions (adjust as needed for your setup)
-- These are suitable for development; tighten for production
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO luthien;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO luthien;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO luthien;

-- Also grant permissions to litellm user (created in previous script)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO litellm;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO litellm;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO litellm;
