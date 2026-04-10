-- Generic policy cache: DB-backed key-value store for policies.
-- Scoped by policy_name so multiple policies can share the table without collision.
CREATE TABLE IF NOT EXISTS policy_cache (
    policy_name TEXT NOT NULL,
    cache_key   TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (policy_name, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_policy_cache_expires ON policy_cache(policy_name, expires_at);
