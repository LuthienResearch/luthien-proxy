-- ABOUTME: Add auth_config table for configurable gateway authentication modes
-- ABOUTME: Supports proxy_key, passthrough, and both auth modes with credential validation settings

CREATE TABLE IF NOT EXISTS auth_config (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- Enforces single row
    auth_mode TEXT NOT NULL DEFAULT 'proxy_key',
    validate_credentials BOOLEAN NOT NULL DEFAULT true,
    valid_cache_ttl_seconds INTEGER NOT NULL DEFAULT 3600,
    invalid_cache_ttl_seconds INTEGER NOT NULL DEFAULT 300,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

-- Seed default row
INSERT INTO auth_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE auth_config IS 'Single-row table storing gateway authentication configuration';
COMMENT ON COLUMN auth_config.auth_mode IS 'Authentication mode: proxy_key (default), passthrough, or both';
COMMENT ON COLUMN auth_config.validate_credentials IS 'Whether to validate passthrough credentials via Anthropic API before accepting';
COMMENT ON COLUMN auth_config.valid_cache_ttl_seconds IS 'TTL for caching valid credentials in Redis (default 1 hour)';
COMMENT ON COLUMN auth_config.invalid_cache_ttl_seconds IS 'TTL for caching invalid credentials in Redis (default 5 minutes)';
