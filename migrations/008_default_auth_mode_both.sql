-- ABOUTME: Change default auth_mode from 'proxy_key' to 'both' for Claude Code OAuth compatibility
-- ABOUTME: Claude Code v2.1+ sends OAuth bearer tokens; 'both' mode accepts proxy key OR passthrough

-- Update existing rows that have the old default
UPDATE auth_config SET auth_mode = 'both' WHERE auth_mode = 'proxy_key';

-- Change the column default for future inserts
ALTER TABLE auth_config ALTER COLUMN auth_mode SET DEFAULT 'both';
