-- ABOUTME: Simplify policy storage to single-row current_policy table
-- ABOUTME: Replaces policy_config table with history/audit trail

CREATE TABLE IF NOT EXISTS current_policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    policy_class_ref TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enabled_at TEXT NOT NULL DEFAULT (datetime('now')),
    enabled_by TEXT
);

-- Migrate active policy from old table (if exists and has data)
INSERT OR IGNORE INTO current_policy (id, policy_class_ref, config, enabled_at, enabled_by)
SELECT 1, policy_class_ref, config, enabled_at, enabled_by
FROM policy_config
WHERE is_active = 1
ORDER BY enabled_at DESC
LIMIT 1;

DROP TABLE IF EXISTS policy_config;
