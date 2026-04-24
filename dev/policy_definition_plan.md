# Policy Definition Table — Plan

## Goal

Introduce a `policy_definition` table that catalogs available policy *types* (definitions), independent of which policy is currently active. This is the first of two layered tables; the future `policy_instance` table will FK into this one and represent (definition + config + name) tuples for revert/sharing/history.

## Out of Scope

- `policy_instance` table (separate PR; depends on this one).
- Modifying `current_policy` to FK into the new tables (separate PR; happens after `policy_instance` exists).
- Custom policy definition types (`defined-in-db`, `ghref`, `policystore`) — schema accommodates them, no implementation.

## Schema

```sql
CREATE TABLE policy_definition (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,            -- kebab-case display id (e.g. "simple-llm")
    description     TEXT,
    definition_type TEXT NOT NULL CHECK (definition_type IN ('built-in', 'defined-in-db', 'ghref', 'policystore')),
    definition_ref  JSONB NOT NULL,                  -- shape varies by definition_type
    config_schema   JSONB,                           -- NULL for built-in (derived at runtime); stored for custom types
    deprecated      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_policy_definition_active ON policy_definition (definition_type) WHERE NOT deprecated;
```

`definition_ref` shape per type:

- `built-in`: `{"module_path": "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"}`
- `defined-in-db`: TBD when implemented
- `ghref`: TBD when implemented (likely `{"repo": "...", "path": "...", "commit": "..."}`)
- `policystore`: TBD when implemented

## Built-in Sync Flow

Source of truth for built-ins is the existing auto-discovery in `src/luthien_proxy/admin/policy_discovery.py::discover_policies()` (filesystem walk over `luthien_proxy.policies` package).

### Name derivation

`derive_builtin_name(class_name: str) -> str`:

1. If suffix is `Policy`, drop it.
2. Insert `-` between any lowercase character followed by an uppercase character.
3. Lowercase the result.

Examples:
- `SimpleLLMPolicy` → `SimpleLLM` → `Simple-LLM` → `simple-llm`
- `NoOpPolicy` → `NoOp` → `No-Op` → `no-op`
- `AllCapsPolicy` → `AllCaps` → `All-Caps` → `all-caps`
- `TextModifierPolicy` → `TextModifier` → `Text-Modifier` → `text-modifier`
- `LLMPolicy` → `LLM` → `LLM` → `llm` (no lowercase→uppercase boundary)

### Description source

Read `__policy_description__: str | None` class attribute. If absent, description is `NULL`. We do **not** fall back to docstring for the new table (kept distinct from the docstring-based extraction in `policy_discovery.extract_description`, which serves the existing admin endpoint and stays unchanged in this PR).

Adding `__policy_description__` to existing built-ins is a follow-up cleanup, not part of this PR.

### Sync algorithm

At app startup, after migrations apply:
1. Call `discover_policies()` to enumerate built-ins from code.
2. For each, compute `name = derive_builtin_name(ClassName)`. Resolve collisions deterministically: sort discovered classes by full module path lexicographically; the first one to claim a `name` wins it, subsequent collisions append `-2`, `-3`, etc. Stable across restarts because module-path order is stable.
3. Upsert into `policy_definition`:
   - `definition_type = 'built-in'`
   - `definition_ref = {"module_path": "<full module path>:<ClassName>"}`
   - `description = <__policy_description__ or NULL>`
   - `config_schema = NULL` (always derived at runtime for built-in)
   - `deprecated = false` (resurrects deprecated rows if a built-in returns)
   - `updated_at = NOW()`
   Upsert key is `name`.
4. Mark any existing `built-in` rows whose names are not in the discovered set as `deprecated = true` (do not delete; instances may FK into them).

Hard-deletion is via direct DB manipulation; no admin endpoint exposed for this initially.

## Schema Validation

When a future `policy_instance` is created, its `config` must validate against the definition's schema:

- For `built-in`: derive Pydantic schema by importing the class via `definition_ref.module_path` and inspecting `__init__`/config model. (`policy_discovery.py` already does this.)
- For other types: use stored `config_schema`.

Validation happens at instance write time (application layer), not as a DB constraint.

## File Changes

### Migrations
- `migrations/postgres/014_add_policy_definition.sql` — DDL above.
- `migrations/sqlite/014_add_policy_definition.sql` — SQLite-translated version (TEXT for JSONB, TEXT for TIMESTAMPTZ, INTEGER for BOOLEAN, `(datetime('now'))` defaults).
- `src/luthien_proxy/utils/sqlite_migrations/014_add_policy_definition.sql` — copy of SQLite version.

### Code
- New module `src/luthien_proxy/policy_definitions.py` (or similar):
  - `derive_builtin_name(class_name: str) -> str` — converts `SimpleLLMPolicy` → `simple-llm`.
  - `sync_builtin_policy_definitions(pool) -> None` — does the upsert + deprecation flow.
- `src/luthien_proxy/main.py` lifespan — call `sync_builtin_policy_definitions` once on startup, after migrations apply.

### Tests
- `tests/luthien_proxy/unit_tests/test_policy_definitions.py`:
  - `test_derive_builtin_name_converts_pascal_to_kebab` — covers `SimpleLLMPolicy` → `simple-llm`, `NoOpPolicy` → `no-op`, `AllCapsPolicy` → `all-caps`, `TextModifierPolicy` → `text-modifier`, `LLMPolicy` → `llm`.
  - `test_derive_builtin_name_drops_only_policy_suffix` — `PolicyManager` (no trailing `Policy`) is unchanged, `MyPolicyHandler` is unchanged (only trailing `Policy`).
  - `test_collision_resolution_is_deterministic` — two classes deriving to same name get `-2` suffix on the lexicographically-later module path; result is stable across runs.
- `tests/luthien_proxy/integration_tests/test_policy_definition_sync.py` (sqlite_e2e tier):
  - `test_sync_seeds_builtin_definitions` — first call writes one row per discovered built-in.
  - `test_sync_is_idempotent` — second call doesn't duplicate, doesn't bump `created_at`.
  - `test_sync_deprecates_missing_builtins` — simulate a built-in disappearing from discovery; row gets `deprecated = true`, not deleted.
  - `test_sync_resurrects_returning_builtin` — deprecated row whose name returns to discovery flips back to `deprecated = false`.
  - `test_definition_ref_module_path_imports_to_correct_class` — for each row, `definition_ref.module_path` resolves to the same class discovery returned.
  - `test_description_pulled_from_attribute` — class with `__policy_description__` set lands in the row; class without it gets NULL.

### Migration Sync Test
- `tests/luthien_proxy/integration_tests/test_migration_sync.py` will validate Postgres + SQLite parity (existing test, just needs the new files in place).

## Open Edge Cases

- **Discovery cache**: `policy_discovery.py` has a module-level `_discovered_policies_cache`. Startup sync should bypass or refresh it if startup happens before any discovery call. Currently the cache populates on first call, so calling `discover_policies()` from startup is fine.
- **Migration ordering**: `policy_definition` is created empty by migration `014`; the startup upsert seeds it. Existing deployments will have an empty table until the first startup with the new code.
- **`current_policy`**: untouched by this PR. Currently has `policy_class_ref TEXT`; later PR will FK to `policy_instance`.

## Invariants

- **Built-in row uniqueness**: `(definition_type='built-in', definition_ref->>'module_path')` is logically unique. No DB constraint enforces this; the sync algorithm guarantees it because (a) `name` is unique by DB constraint and (b) we upsert by `name` derived deterministically from class name + module path tiebreaker.
- **No hard deletion via app code**: built-in sync only inserts/updates/deprecates. Deletion is a DB-admin operation.
- **Class refs stay valid**: every non-deprecated `built-in` row's `module_path` must import to a `BasePolicy` subclass at app startup time. Sync skips (and logs) any class that fails this check.

## Assumptions

- `discover_policies()` is the sole source of truth for built-ins and accurately reflects what's importable.
- Adding a new column to `current_policy` later (FK to `policy_instance`) is feasible without ALTER TABLE issues on SQLite — true on SQLite 3.35+ which we have.
- Name collisions across built-ins are rare enough that suffix-numbering is acceptable v1 behavior.
