-- ABOUTME: Change default auth_mode from 'proxy_key' to 'both'
-- ABOUTME: Claude Code v2.1+ sends OAuth bearer tokens

UPDATE auth_config SET auth_mode = 'both' WHERE auth_mode = 'proxy_key';

-- SQLite doesn't support ALTER COLUMN SET DEFAULT, but new inserts
-- use the CREATE TABLE default. Since auth_config is single-row and
-- already seeded, this UPDATE is sufficient.
