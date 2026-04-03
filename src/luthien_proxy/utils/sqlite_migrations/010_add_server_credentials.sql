-- Server credential storage for operator-provisioned API keys.
-- Used by policies that declare an auth_provider referencing a server key.

CREATE TABLE IF NOT EXISTS server_credentials (
    name TEXT UNIQUE NOT NULL PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'anthropic',
    platform_url TEXT,
    credential_type TEXT NOT NULL DEFAULT 'api_key',
    credential_value TEXT NOT NULL,
    is_encrypted INTEGER NOT NULL DEFAULT 0,
    expiry TEXT,
    owner TEXT,
    scope TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_server_credentials_platform ON server_credentials(platform);
