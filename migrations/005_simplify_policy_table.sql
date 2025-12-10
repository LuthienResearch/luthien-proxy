-- ABOUTME: Simplify policy storage to single-row current_policy table
-- ABOUTME: Replaces policy_config table with history/audit trail

-- Create new simplified current_policy table
CREATE TABLE IF NOT EXISTS current_policy (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- Enforces single row
    policy_class_ref TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    enabled_at TIMESTAMP NOT NULL DEFAULT NOW(),
    enabled_by TEXT
);

-- Migrate active policy from old table (if exists)
INSERT INTO current_policy (id, policy_class_ref, config, enabled_at, enabled_by)
SELECT 1, policy_class_ref, config, enabled_at, enabled_by
FROM policy_config
WHERE is_active = true
ORDER BY enabled_at DESC
LIMIT 1
ON CONFLICT (id) DO NOTHING;

-- Drop the old table
DROP TABLE IF EXISTS policy_config;

-- Add documentation
COMMENT ON TABLE current_policy IS 'Single-row table storing the currently active policy configuration';
COMMENT ON COLUMN current_policy.id IS 'Always 1 - enforces single row via CHECK constraint';
COMMENT ON COLUMN current_policy.policy_class_ref IS 'Full module path to policy class (e.g., luthien_proxy.policies.all_caps_policy:AllCapsPolicy)';
COMMENT ON COLUMN current_policy.config IS 'JSON configuration parameters for the policy';
COMMENT ON COLUMN current_policy.enabled_at IS 'Timestamp when policy was enabled';
COMMENT ON COLUMN current_policy.enabled_by IS 'Identifier of who/what enabled the policy (user, admin, startup, etc.)';
