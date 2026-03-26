-- ABOUTME: Add policy_config table for runtime policy management
-- ABOUTME: Stores policy configuration with history and audit trail

-- Create policy_config table
CREATE TABLE IF NOT EXISTS policy_config (
    id SERIAL PRIMARY KEY,
    policy_class_ref TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    enabled_at TIMESTAMP NOT NULL DEFAULT NOW(),
    enabled_by TEXT,
    is_active BOOLEAN DEFAULT false,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Ensure only one active policy at a time
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_policy
ON policy_config (is_active)
WHERE is_active = true;

-- Index for querying history
CREATE INDEX IF NOT EXISTS idx_policy_enabled_at ON policy_config (enabled_at DESC);

-- Index for querying by class reference
CREATE INDEX IF NOT EXISTS idx_policy_class_ref ON policy_config (policy_class_ref);

-- Add comment for documentation
COMMENT ON TABLE policy_config IS 'Runtime policy configuration with history and audit trail';
COMMENT ON COLUMN policy_config.policy_class_ref IS 'Full module path to policy class (e.g., luthien_proxy.policies.all_caps_policy:AllCapsPolicy)';
COMMENT ON COLUMN policy_config.config IS 'JSON configuration parameters for the policy';
COMMENT ON COLUMN policy_config.enabled_at IS 'Timestamp when policy was enabled';
COMMENT ON COLUMN policy_config.enabled_by IS 'Identifier of who/what enabled the policy (user, admin, file-sync, etc.)';
COMMENT ON COLUMN policy_config.is_active IS 'Whether this is the currently active policy (only one can be active)';
