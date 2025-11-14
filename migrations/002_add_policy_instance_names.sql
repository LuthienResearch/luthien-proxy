-- ABOUTME: Add support for named policy instances
-- ABOUTME: Allows creating multiple saved configurations and switching between them

-- Add name column for user-friendly policy instance identification
ALTER TABLE policy_config ADD COLUMN IF NOT EXISTS name TEXT;

-- Add description column for policy instance documentation
ALTER TABLE policy_config ADD COLUMN IF NOT EXISTS description TEXT;

-- Create unique index on name to prevent duplicates
-- Allow NULL names for backward compatibility with unnamed instances
CREATE UNIQUE INDEX idx_policy_config_name
ON policy_config (name)
WHERE name IS NOT NULL;

-- Update existing records to have unique names based on their ID
UPDATE policy_config
SET name = 'policy-' || id::text
WHERE name IS NULL;

-- Add comments for documentation
COMMENT ON COLUMN policy_config.name IS 'User-friendly name for this policy instance (e.g., "strict-judge", "development-noop")';
COMMENT ON COLUMN policy_config.description IS 'Optional description of what this policy instance does or when to use it';
