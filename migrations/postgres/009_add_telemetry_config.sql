-- ABOUTME: Add telemetry_config table for usage telemetry opt-out and deployment identity
-- ABOUTME: Single-row table storing telemetry enabled state and unique deployment ID

CREATE TABLE IF NOT EXISTS telemetry_config (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled BOOLEAN,  -- null = use default (enabled)
    deployment_id UUID NOT NULL DEFAULT gen_random_uuid(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT
);

-- Seed with a single row so deployment_id is generated immediately
INSERT INTO telemetry_config (id) VALUES (1) ON CONFLICT DO NOTHING;
