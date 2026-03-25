-- ABOUTME: Add policy_config table for runtime policy management
-- ABOUTME: Stores policy configuration with history and audit trail

CREATE TABLE IF NOT EXISTS policy_config (
    id INTEGER PRIMARY KEY,
    policy_class_ref TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enabled_at TEXT NOT NULL DEFAULT (datetime('now')),
    enabled_by TEXT,
    is_active INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_policy
ON policy_config (is_active)
WHERE is_active = 1;

CREATE INDEX IF NOT EXISTS idx_policy_enabled_at ON policy_config (enabled_at DESC);
CREATE INDEX IF NOT EXISTS idx_policy_class_ref ON policy_config (policy_class_ref);
