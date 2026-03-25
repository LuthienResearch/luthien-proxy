-- ABOUTME: Add auth_config table for configurable gateway authentication modes
-- ABOUTME: Supports proxy_key, passthrough, and both auth modes

CREATE TABLE IF NOT EXISTS auth_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    auth_mode TEXT NOT NULL DEFAULT 'proxy_key',
    validate_credentials INTEGER NOT NULL DEFAULT 1,
    valid_cache_ttl_seconds INTEGER NOT NULL DEFAULT 3600,
    invalid_cache_ttl_seconds INTEGER NOT NULL DEFAULT 300,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by TEXT
);

INSERT OR IGNORE INTO auth_config (id) VALUES (1);
