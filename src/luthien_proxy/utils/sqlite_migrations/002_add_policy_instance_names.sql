-- ABOUTME: Add support for named policy instances
-- ABOUTME: Allows creating multiple saved configurations and switching between them

ALTER TABLE policy_config ADD COLUMN name TEXT;
ALTER TABLE policy_config ADD COLUMN description TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_config_name
ON policy_config (name)
WHERE name IS NOT NULL;

UPDATE policy_config
SET name = 'policy-' || CAST(id AS TEXT)
WHERE name IS NULL;
