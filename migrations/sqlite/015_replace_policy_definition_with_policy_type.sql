DROP TABLE IF EXISTS policy_definition;

CREATE TABLE IF NOT EXISTS policy_type (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    definition_type TEXT NOT NULL CHECK (definition_type IN ('built-in')),
    module_path     TEXT,
    config_schema   TEXT,
    deprecated      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),

    CONSTRAINT policy_type_builtin_module_path_required
        CHECK (definition_type <> 'built-in' OR module_path IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_type_builtin_module_path
    ON policy_type (module_path)
    WHERE definition_type = 'built-in';

CREATE INDEX IF NOT EXISTS idx_policy_type_active
    ON policy_type (definition_type)
    WHERE deprecated = 0;
