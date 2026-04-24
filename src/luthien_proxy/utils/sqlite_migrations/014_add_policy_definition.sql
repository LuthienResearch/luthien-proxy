-- Add policy_definition table — catalog of available policy types
-- Decoupled from current_policy. A future policy_instance table will FK into this

CREATE TABLE IF NOT EXISTS policy_definition (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    definition_type TEXT NOT NULL CHECK (definition_type IN ('built-in', 'defined-in-db', 'ghref', 'policystore')),
    definition_ref  TEXT NOT NULL,
    config_schema   TEXT,
    deprecated      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_policy_definition_active ON policy_definition (definition_type) WHERE deprecated = 0;
