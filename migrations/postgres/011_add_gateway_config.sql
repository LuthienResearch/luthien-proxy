-- Key-value store for runtime-configurable gateway settings.
-- Used by the config registry for DB-settable config values.
-- Priority: CLI args > env vars > gateway_config > defaults.

CREATE TABLE IF NOT EXISTS gateway_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);
