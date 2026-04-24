-- ABOUTME: Replace 014's policy_definition with v5 schema (policy_type)
-- ABOUTME: Drops the prior table; class_ref is the stable identity for built-in types

DROP TABLE IF EXISTS policy_definition;

CREATE TABLE IF NOT EXISTS policy_type (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    definition_type TEXT NOT NULL CHECK (definition_type IN ('built-in')),
    class_ref       TEXT,
    config_schema   JSONB,
    deprecated      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT policy_type_builtin_class_ref_required
        CHECK (definition_type <> 'built-in' OR class_ref IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_type_builtin_class_ref
    ON policy_type (class_ref)
    WHERE definition_type = 'built-in';

CREATE INDEX IF NOT EXISTS idx_policy_type_active
    ON policy_type (definition_type)
    WHERE NOT deprecated;

COMMENT ON TABLE policy_type IS 'Registry of available policy types (built-in only in v1). class_ref is the stable identifier for built-in types.';
COMMENT ON COLUMN policy_type.name IS 'Display-only kebab-case name, unique but not used for lookups.';
COMMENT ON COLUMN policy_type.definition_type IS 'Discriminator: currently only built-in. Future types will be added here.';
COMMENT ON COLUMN policy_type.class_ref IS 'Module:Class reference (e.g., "luthien_proxy.policies.noop_policy:NoOpPolicy"). Unique per built-in. Null for non-built-in types.';
COMMENT ON COLUMN policy_type.config_schema IS 'JSON schema for the policy config (null for built-in, derives from class).';
COMMENT ON COLUMN policy_type.deprecated IS 'True when a built-in is no longer in the registered list; never hard-deleted.';
