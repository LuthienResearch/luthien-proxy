-- Server credential storage for operator-provisioned API keys.
-- Used by policies that declare an auth_provider referencing a server key.

CREATE TABLE IF NOT EXISTS server_credentials (
    name TEXT UNIQUE NOT NULL PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'anthropic',
    platform_url TEXT,
    credential_type TEXT NOT NULL DEFAULT 'api_key',
    credential_value TEXT NOT NULL,
    is_encrypted BOOLEAN NOT NULL DEFAULT FALSE,
    expiry TIMESTAMPTZ,
    owner TEXT,
    scope TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_server_credentials_platform ON server_credentials(platform);
