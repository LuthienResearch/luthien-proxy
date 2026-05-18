-- ABOUTME: Re-assert auth_config.auth_mode DEFAULT 'both' after removing legacy 'proxy_key' tolerance
-- ABOUTME: Idempotent safeguard so a rebuild from scratch + partial replay can't resurrect 'proxy_key'

-- Migration 008 already set the default to 'both', but the original CREATE TABLE
-- in migration 007 declared DEFAULT 'proxy_key'. With the in-code tolerance
-- removed, any row inserted without an explicit auth_mode on a database that
-- somehow missed 008 would crash the gateway. This statement is a no-op in the
-- normal upgrade path and a guardrail in every other path.
ALTER TABLE auth_config ALTER COLUMN auth_mode SET DEFAULT 'both';
