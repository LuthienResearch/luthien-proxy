-- Generic policy cache: DB-backed key-value store for policies.
-- Scoped by policy_name so multiple policies can share the table without collision.
CREATE TABLE IF NOT EXISTS policy_cache (
    policy_name TEXT NOT NULL,
    cache_key   TEXT NOT NULL,
    value_json  JSONB NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (policy_name, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_policy_cache_expires ON policy_cache(policy_name, expires_at);
