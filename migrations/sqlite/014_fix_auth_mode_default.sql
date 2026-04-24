-- ABOUTME: Recreate auth_config with DEFAULT 'both' after removing legacy 'proxy_key' tolerance
-- ABOUTME: SQLite can't ALTER COLUMN DEFAULT, so we table-swap to close the footgun

-- Migration 007 created the table with DEFAULT 'proxy_key'. Migration 008
-- UPDATEd the single seeded row to 'both' but could not alter the column
-- default (SQLite limitation). With in-code tolerance now removed, any future
-- INSERT that omits auth_mode would resurrect 'proxy_key' and crash the
-- gateway on startup. Recreate the table with DEFAULT 'both' to close that gap.

CREATE TABLE auth_config_new (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    auth_mode TEXT NOT NULL DEFAULT 'both',
    validate_credentials INTEGER NOT NULL DEFAULT 1,
    valid_cache_ttl_seconds INTEGER NOT NULL DEFAULT 3600,
    invalid_cache_ttl_seconds INTEGER NOT NULL DEFAULT 300,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by TEXT
);

INSERT INTO auth_config_new (id, auth_mode, validate_credentials, valid_cache_ttl_seconds, invalid_cache_ttl_seconds, updated_at, updated_by)
SELECT id, auth_mode, validate_credentials, valid_cache_ttl_seconds, invalid_cache_ttl_seconds, updated_at, updated_by
FROM auth_config;

DROP TABLE auth_config;

ALTER TABLE auth_config_new RENAME TO auth_config;
