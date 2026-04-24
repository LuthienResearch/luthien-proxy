-- Add policy_definition table — catalog of available policy types
-- Decoupled from current_policy; future policy_instance table will FK into this

CREATE TABLE IF NOT EXISTS policy_definition (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    definition_type TEXT NOT NULL CHECK (definition_type IN ('built-in', 'defined-in-db', 'ghref', 'policystore')),
    definition_ref  JSONB NOT NULL,
    config_schema   JSONB,
    deprecated      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_definition_active ON policy_definition (definition_type) WHERE NOT deprecated;

COMMENT ON TABLE policy_definition IS 'Registry of available policy types (built-in or custom). Future policy_instance table FKs into this.';
COMMENT ON COLUMN policy_definition.name IS 'Stable kebab-case identifier, unique';
COMMENT ON COLUMN policy_definition.definition_type IS 'Discriminator: built-in | defined-in-db | ghref | policystore';
COMMENT ON COLUMN policy_definition.definition_ref IS 'Type-specific reference. For built-in: {"module_path": "<full module>:<ClassName>"}';
COMMENT ON COLUMN policy_definition.config_schema IS 'NULL for built-in (derived at runtime from class); stored for custom types';
COMMENT ON COLUMN policy_definition.deprecated IS 'True when no longer in code; never hard-deleted by app';
