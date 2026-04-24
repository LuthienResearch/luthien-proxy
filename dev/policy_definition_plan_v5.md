# Policy Type Table — Plan v5 (post-devil-4)

Supersedes `dev/policy_definition_plan_v4.md`. Devil-4 endorsed v4 with 4 required changes; this plan addresses them. v4 is the canonical reference for all unchanged decisions; only diffs are documented here.

## Devil-4 punch list → response

| # | Required change | Response |
|---|---|---|
| R1 | "21-class survey" was partially fabricated; included `SimplePolicy` which discovery filters out | **Fix.** Re-ran `discover_policies()` for the third time, captured the actual 21 entries verbatim. `SimplePolicy` is NOT in the output (filtered at `policy_discovery.py:31` in `SKIP_MODULES` and at `policy_discovery.py:496-497` by name). v4's table contained 22 rows including the spurious `SimplePolicy`; corrected below. |
| R2 | Drop `SimplePolicy` from `REGISTERED_BUILTINS`, or justify why a base class belongs in a user-facing registry | **Drop.** Devil's reasoning is correct: `SimplePolicy.__init__(*args, **kwargs)` is an authoring abstract with no concrete config. `config/policy_config.yaml` references it only in a commented-out example. Discovery deliberately filters it. Including it in the registry would let a user select a non-functional policy from a future UI dropdown. Removed from `REGISTERED_BUILTINS`. |
| R3 | Migration-rename plan was wrong about hash-check behavior — SQLite has no DB→local check, so renaming silently leaves orphaned state on dev DBs that already applied old-014 | **Fix.** Revert to the v3 approach: keep `014_*` files unchanged, add new `015_replace_policy_definition_with_policy_type.sql` that does `DROP TABLE IF EXISTS policy_definition` then `CREATE TABLE policy_type (...)`. Devil-3's pedantic concern about "two migrations on every fresh deploy" is microseconds; devil-4's concern about silent inconsistent state is real. The DROP IF EXISTS handles dev DBs that ran 014; fresh deploys run both 014 then 015 sequentially. Postgres `MISSING LOCAL FILES` check is not triggered (014 still exists). |
| R4 | `INTEGER PRIMARY KEY AUTOINCREMENT` contradicts AGENTS.md guidance for the wrong reason | **Fix.** SQLite migration uses `INTEGER PRIMARY KEY` (no AUTOINCREMENT) per `migrations/AGENTS.md`. The fixture loads the migration directly per devil-1 #9, so they remain aligned automatically. AUTOINCREMENT's "never reuse rowids after delete" guarantee buys nothing here because rows are deprecated, not deleted. |

## Schema (v5)

### Postgres (`migrations/postgres/015_replace_policy_definition_with_policy_type.sql`)

```sql
-- ABOUTME: Replace 014's policy_definition with v5 schema (policy_type)
-- ABOUTME: Drops the prior table; module_path is a real column with partial unique index

DROP TABLE IF EXISTS policy_definition;

CREATE TABLE IF NOT EXISTS policy_type (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    definition_type TEXT NOT NULL CHECK (definition_type IN ('built-in')),
    module_path     TEXT,
    config_schema   JSONB,
    deprecated      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT policy_type_builtin_module_path_required
        CHECK (definition_type <> 'built-in' OR module_path IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_type_builtin_module_path
    ON policy_type (module_path)
    WHERE definition_type = 'built-in';

CREATE INDEX IF NOT EXISTS idx_policy_type_active
    ON policy_type (definition_type)
    WHERE NOT deprecated;
```

### SQLite (`migrations/sqlite/015_replace_policy_definition_with_policy_type.sql` and runtime copy)

```sql
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
```

## Registered built-ins (v5, corrected)

Authoritative output of `discover_policies()` (just-ran): **21 entries.** Allowlist below marks 18 True, 3 False.

| class_ref | Decision |
|---|---|
| `luthien_proxy.policies.all_caps_policy:AllCapsPolicy` | True |
| `luthien_proxy.policies.conversation_link_policy:ConversationLinkPolicy` | True |
| `luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy` | True |
| `luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy` | True |
| `luthien_proxy.policies.hackathon_onboarding_policy:HackathonOnboardingPolicy` | False |
| `luthien_proxy.policies.hackathon_policy_template:HackathonPolicy` | False |
| `luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy` | True |
| `luthien_proxy.policies.noop_policy:NoOpPolicy` | True |
| `luthien_proxy.policies.onboarding_policy:OnboardingPolicy` | True |
| `luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy` | True |
| `luthien_proxy.policies.presets.block_sensitive_file_writes:BlockSensitiveFileWritesPolicy` | True |
| `luthien_proxy.policies.presets.block_web_requests:BlockWebRequestsPolicy` | True |
| `luthien_proxy.policies.presets.no_apologies:NoApologiesPolicy` | True |
| `luthien_proxy.policies.presets.no_yapping:NoYappingPolicy` | True |
| `luthien_proxy.policies.presets.plain_dashes:PlainDashesPolicy` | True |
| `luthien_proxy.policies.presets.prefer_uv:PreferUvPolicy` | True |
| `luthien_proxy.policies.sample_pydantic_policy:SamplePydanticPolicy` | False |
| `luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy` | True |
| `luthien_proxy.policies.simple_noop_policy:SimpleNoOpPolicy` | True |
| `luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy` | True |
| `luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy` | True |

`REGISTERED_BUILTINS` in `policy_types.py` contains exactly the 18 True entries above, in the order shown.

## Sync algorithm

Unchanged from v4 (signature, exception handling, description cascade, name resolution). Only the contents of `REGISTERED_BUILTINS` and migration filename string change.

## Lifespan wiring

Unchanged from v4: sync is **not** invoked from `main.py`. PR #2 wires it in.

## Test changes

Unchanged from v4. The `test_registered_builtins_have_no_name_collisions` test runs against the 18-entry list and should pass (no two entries derive to the same kebab name; verified mentally — each class name is distinct).

The integration-test fixture loads `src/luthien_proxy/utils/sqlite_migrations/015_replace_policy_definition_with_policy_type.sql` (not 014) — but should also apply 014 first to produce the same state as a fresh deploy. Easier: load **all** SQLite migration files in numeric order, just like the runtime code path. That's what the existing migration-replay helper does in `migration_check.py`; reuse it.

## File changes (v5)

### New files
- `migrations/postgres/015_replace_policy_definition_with_policy_type.sql`
- `migrations/sqlite/015_replace_policy_definition_with_policy_type.sql`
- `src/luthien_proxy/utils/sqlite_migrations/015_replace_policy_definition_with_policy_type.sql`
- `src/luthien_proxy/policy_types.py` (rename + rewrite of existing `policy_definitions.py`)
- `tests/luthien_proxy/unit_tests/test_policy_types.py` (rename + minor updates)
- `tests/luthien_proxy/integration_tests/test_policy_type_sync.py` (rename + fixture loads real migrations)

### Deleted
- `src/luthien_proxy/policy_definitions.py` (replaced by `policy_types.py`)
- `tests/luthien_proxy/unit_tests/test_policy_definitions.py` (replaced)
- `tests/luthien_proxy/integration_tests/test_policy_definition_sync.py` (replaced)

### Modified
- `src/luthien_proxy/main.py` — remove the v1 `sync_builtin_policy_definitions` import + call. No production wiring of sync in this PR.

### NOT modified
- `migrations/postgres/014_add_policy_definition.sql` — stays as-is (creates the soon-defunct table; 015 drops it)
- `migrations/sqlite/014_add_policy_definition.sql` — stays
- `src/luthien_proxy/utils/sqlite_migrations/014_add_policy_definition.sql` — stays
- All built-in policy classes (no markers needed; allowlist is in `policy_types.py`)
- `policy_discovery.py` — sync is decoupled from discovery via the explicit allowlist

## Migration sequencing (worked example)

**Fresh deploy:**
1. `000_init` ... `013_*` — applied
2. `014_add_policy_definition.sql` — creates `policy_definition` (vapor table, 50ms cost)
3. `015_replace_policy_definition_with_policy_type.sql` — drops `policy_definition`, creates `policy_type`

**Dev DB that already ran 014:**
1. `_migrations` has rows through `014_add_policy_definition.sql`. `policy_definition` table exists with no rows (sync was never wired).
2. On startup, runner sees `015_replace_*` is unknown to DB, applies it. `DROP TABLE IF EXISTS policy_definition` cleans up. `CREATE TABLE policy_type` creates the new table. `_migrations` gets a new row.
3. Clean state. No orphaned tables, no orphaned rows.

**Dev DB that already ran the v3-attempt rewrite of 014:** N/A — that migration only existed in plans, never on disk.

## Invariants (v5)

Unchanged from v4. Restated:

- Identity for built-in is `(definition_type='built-in', module_path)`, enforced by partial unique index plus `CHECK` for non-NULL `module_path` when `built-in`.
- `name` is display-only. Lookups MUST be by `id` or `module_path`, never `name`.
- `REGISTERED_BUILTINS` in `policy_types.py` is the source of truth.
- Caught exceptions in sync: exactly `(ImportError, AttributeError, ValueError)`. DB exceptions propagate.
- Sync is not wired into `main.py`.

## Assumptions (v5)

- The 18-entry allowlist accurately reflects user-deployable policies as of today (verified against actual `discover_policies()` output).
- SQLite supports `ON CONFLICT (col) WHERE ... DO UPDATE` (3.24+; we have 3.45+); `_translate_params` doesn't need to touch this syntax.
- `_translate_params` correctly translates `$N` → `?` for the upsert SQL (verified via existing `policy_definitions.py` v1 pattern).
- Migration runner applies new files in lexicographic order from disk and records their filenames in `_migrations`. Adding 015 alongside an unchanged 014 produces a clean state on both fresh and existing dev DBs.

## Out of scope

Unchanged from v4: `policy_instance`, non-built-in definition types, sync wiring into lifespan, `current_policy` FK, hard-delete endpoint, constructibility validation, changes to `policy_discovery.py`.
