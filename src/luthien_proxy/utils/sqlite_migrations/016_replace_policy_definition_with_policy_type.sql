DROP TABLE IF EXISTS policy_definition;

CREATE TABLE IF NOT EXISTS policy_type (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    definition_type TEXT NOT NULL CHECK (definition_type IN ('built-in')),
    class_ref       TEXT,
    config_schema   TEXT,
    deprecated      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),

    CONSTRAINT policy_type_builtin_class_ref_required
        CHECK (definition_type <> 'built-in' OR class_ref IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_type_builtin_class_ref
    ON policy_type (class_ref)
    WHERE definition_type = 'built-in';

CREATE INDEX IF NOT EXISTS idx_policy_type_active
    ON policy_type (definition_type)
    WHERE deprecated = 0;
