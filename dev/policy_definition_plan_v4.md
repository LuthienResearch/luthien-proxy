# Policy Type Table — Plan v4 (post-devil-3)

Supersedes `dev/policy_definition_plan_v3.md`. Devil-3 **rejected** v3 with structural concerns; this plan addresses them. Predecessors: `dev/policy_definition_plan.md` (v1), `_v2.md`, `_v3.md`.

## Devil-3 punch list → response

| # | Devil-3 concern | Response |
|---|---|---|
| 1 | Survey missed `policies/presets/*` (7 classes); demonstrates author hasn't internalized the discovery shape | **Fix.** Re-surveyed by *running* `discover_policies()`. Real output: 21 classes (listed below). v4 uses an explicit allowlist instead of an opt-in attribute, so the failure mode for "missed a class" is reviewable in a single file diff. |
| 2 | `_should_register` re-imports modules that discovery already imported; entry shape drops the class object | **Fix.** Allowlist eliminates `_should_register` entirely. Sync loop does one `importlib.import_module` per allowlisted class (cheap due to import cache, but only one site). |
| 3 | `getattr(cls, "__policy_registered__", False)` walks MRO; subclasses inherit the marker | **Fix by removal.** Allowlist replaces the attribute mechanism. No MRO ambiguity exists when the source of truth is an explicit list of `class_ref` strings. (Verified: all 7 `policies/presets/*` classes inherit from `SimpleLLMPolicy`. Marking the base would have auto-registered all 7 plus `HackathonPolicy` which also inherits from `SimplePolicy`.) |
| 4 | Upsert SQL portability between PG and SQLite (`$N`/`?` translation, ON CONFLICT WHERE syntax) | **Verify, don't assume.** The codebase's SQLite adapter (`db_sqlite.py:_translate_params`) auto-translates `$N` → `?` and strips `::type` casts. v3's existing `policy_definitions.py` already uses `$N` placeholders successfully against SQLite. Will explicitly run the v4 upsert against both backends in tests; if `ON CONFLICT (col) WHERE ...` syntax breaks on SQLite, fall back to compute-existence-and-INSERT-or-UPDATE in Python. |
| 5 | Migration 015 worse than edit-014-in-place; 014 hasn't shipped; 015 leaves a vapor CHECK constraint on disk | **Fix.** Edit `014_add_policy_definition.sql` files in place (rename to `014_add_policy_type.sql`). Nothing has shipped — devil-3 is right that this is one developer's local state, not a migration-immutability concern. Drop migration 015 entirely. |
| 6 | Sync unwired = dead code with no end-to-end exercise; latent integration bug surface | **Pushback (preserved from v3).** Acknowledged: integration tests use a fresh per-test pool; production lifespan would use a long-lived pool. Counter: the sync function is small, fully exercised against real SQLite at the API level, and the wiring (single `await sync_policy_types(db_pool)` line in lifespan) is trivial enough that PR #2's tests will catch any latent issue. The alternative (wire it now without a reader) reintroduces the brick-the-gateway risk for zero current benefit. |
| 7 | Two-tier exception handling has fuzzy criteria — schema bugs end up in the "quiet" tier | **Fix.** Catch by **exception type**, not control-flow location. `ImportError`, `AttributeError`, `ValueError` (per-class import failures) → log + skip. Database-side exceptions (`asyncpg.PostgresError`, `sqlite3.IntegrityError`, `aiosqlite` errors) are NOT caught — they propagate. The `except Exception` in v3 is replaced with a narrow tuple. |
| 8 | Filename backstop incomplete (doesn't catch `hackathon_onboarding_policy.py`); half-suspenders is worst-of-both | **Fix by removal.** Allowlist is the only mechanism. No filename patterns. |
| 9 | `module_path` column duplicates `definition_ref->>'module_path'` — discriminated record with leaky representation | **Fix.** Drop `definition_ref` JSONB column from this PR. Built-in stores `module_path` in a real column. When `ghref`/`defined-in-db`/`policystore` land, each adds the columns it actually needs (or introduces JSONB then). YAGNI applied to the JSONB column specifically; discriminator (`definition_type`) is kept. |
| 10 | Description cascade depends on `extract_description` falsy return (undocumented coupling) | **Fix.** Explicit branching, no `or`-chain. |

## Surveyed built-in policies (real `discover_policies()` output)

Ran `discover_policies()` against the current codebase. 21 classes. Allowlist-marked True/False explicitly:

| class_ref | Decision | Rationale |
|---|---|---|
| `luthien_proxy.policies.all_caps_policy:AllCapsPolicy` | True | Real |
| `luthien_proxy.policies.conversation_link_policy:ConversationLinkPolicy` | True | Real |
| `luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy` | True | Real |
| `luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy` | True | Real |
| `luthien_proxy.policies.hackathon_onboarding_policy:HackathonOnboardingPolicy` | False | Hackathon demo |
| `luthien_proxy.policies.hackathon_policy_template:HackathonPolicy` | False | Template ("[describe what it does here]") |
| `luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy` | True | Real (composer) |
| `luthien_proxy.policies.noop_policy:NoOpPolicy` | True | Real (default) |
| `luthien_proxy.policies.onboarding_policy:OnboardingPolicy` | True | Real |
| `luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy` | True | Real (preset) |
| `luthien_proxy.policies.presets.block_sensitive_file_writes:BlockSensitiveFileWritesPolicy` | True | Real (preset) |
| `luthien_proxy.policies.presets.block_web_requests:BlockWebRequestsPolicy` | True | Real (preset) |
| `luthien_proxy.policies.presets.no_apologies:NoApologiesPolicy` | True | Real (preset) |
| `luthien_proxy.policies.presets.no_yapping:NoYappingPolicy` | True | Real (preset) |
| `luthien_proxy.policies.presets.plain_dashes:PlainDashesPolicy` | True | Real (preset) |
| `luthien_proxy.policies.presets.prefer_uv:PreferUvPolicy` | True | Real (preset) |
| `luthien_proxy.policies.sample_pydantic_policy:SamplePydanticPolicy` | False | Sample/example |
| `luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy` | True | Real (base for presets) |
| `luthien_proxy.policies.simple_noop_policy:SimpleNoOpPolicy` | True | Real |
| `luthien_proxy.policies.simple_policy:SimplePolicy` | True | Real (base) |
| `luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy` | True | Real |
| `luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy` | True | Real |

**18 registered, 3 skipped.** This list is the canonical source of truth in `policy_types.py:REGISTERED_BUILTINS`.

## Schema (v4)

### Postgres (`migrations/postgres/014_add_policy_type.sql` — replaces existing 014 file)

```sql
-- ABOUTME: Add policy_type table — registry of available policy types
-- ABOUTME: Future policy_instance table will FK into this; current_policy untouched in this PR

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

### SQLite (`migrations/sqlite/014_add_policy_type.sql` and runtime copy)

```sql
CREATE TABLE IF NOT EXISTS policy_type (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
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

`AUTOINCREMENT` here is intentional — devil-1 #9 noted the test fixture used it while the migration didn't. Aligning to AUTOINCREMENT in the migration so fixture (which loads the migration) and schema match. Per `migrations/AGENTS.md`, `AUTOINCREMENT` is the SQLite mirror of Postgres `SERIAL` semantics for ID stability across deletes.

## Sync algorithm (v4)

```python
# In src/luthien_proxy/policy_types.py

REGISTERED_BUILTINS: tuple[str, ...] = (
    "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
    "luthien_proxy.policies.conversation_link_policy:ConversationLinkPolicy",
    "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy",
    "luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy",
    "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy",
    "luthien_proxy.policies.noop_policy:NoOpPolicy",
    "luthien_proxy.policies.onboarding_policy:OnboardingPolicy",
    "luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy",
    "luthien_proxy.policies.presets.block_sensitive_file_writes:BlockSensitiveFileWritesPolicy",
    "luthien_proxy.policies.presets.block_web_requests:BlockWebRequestsPolicy",
    "luthien_proxy.policies.presets.no_apologies:NoApologiesPolicy",
    "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy",
    "luthien_proxy.policies.presets.plain_dashes:PlainDashesPolicy",
    "luthien_proxy.policies.presets.prefer_uv:PreferUvPolicy",
    "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy",
    "luthien_proxy.policies.simple_noop_policy:SimpleNoOpPolicy",
    "luthien_proxy.policies.simple_policy:SimplePolicy",
    "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
    "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
)


async def sync_policy_types(
    pool: DatabasePool,
    *,
    class_refs: Iterable[str] = REGISTERED_BUILTINS,
) -> None:
    """Upsert policy_type rows from the registered built-in list.

    Marks any existing built-in row whose module_path is not in `class_refs` as deprecated.

    Per-class import failures are logged and skipped. Database-level exceptions propagate
    (table missing, constraint violations, connection errors are signal — not noise).
    """
    resolved = resolve_collisions([
        {"class_ref": ref, "name": ref.split(":", 1)[1]}
        for ref in class_refs
    ])
    now_iso = datetime.now(UTC).isoformat()
    seen_module_paths: list[str] = []

    async with pool.connection() as conn:
        for assigned_name, entry in resolved:
            class_ref = entry["class_ref"]
            try:
                module_path, class_name = class_ref.split(":", 1)
                module = importlib.import_module(module_path)
                policy_class = getattr(module, class_name)
            except (ImportError, AttributeError, ValueError) as exc:
                logger.warning(f"Skipping {class_ref}: {exc}")
                continue

            if not (isinstance(policy_class, type) and issubclass(policy_class, BasePolicy)):
                logger.warning(f"Skipping {class_ref}: not a BasePolicy subclass")
                continue

            description = _resolve_description(policy_class)

            await conn.execute(
                """
                INSERT INTO policy_type
                    (name, description, definition_type, module_path,
                     config_schema, deprecated, updated_at)
                VALUES ($1, $2, 'built-in', $3, $4, $5, $6)
                ON CONFLICT (module_path) WHERE definition_type = 'built-in' DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    deprecated = EXCLUDED.deprecated,
                    updated_at = EXCLUDED.updated_at
                """,
                assigned_name,
                description,
                class_ref,
                None,    # config_schema
                False,   # deprecated
                now_iso,
            )
            seen_module_paths.append(class_ref)

        # Mark previously-registered built-ins that aren't in the current allowlist as deprecated
        existing = await conn.fetch(
            "SELECT module_path FROM policy_type WHERE definition_type = 'built-in'"
        )
        existing_paths = {row["module_path"] for row in existing}
        to_deprecate = existing_paths - set(seen_module_paths)
        for path in to_deprecate:
            await conn.execute(
                "UPDATE policy_type SET deprecated = $1 WHERE module_path = $2",
                True,
                path,
            )


def _resolve_description(policy_class: type) -> str | None:
    """Return description from explicit attribute, then docstring, then None.

    Explicit branching — does not depend on extract_description's empty-string return.
    """
    explicit = getattr(policy_class, "__policy_description__", None)
    if explicit is not None:
        return explicit
    doc = extract_description(policy_class)
    if doc:
        return doc
    return None
```

`derive_builtin_name` and `resolve_collisions` are unchanged from v1/v2 (they still produce display names; `resolve_collisions` no longer carries cross-rename stability requirements since `name` is display-only).

## Lifespan wiring (v4)

`src/luthien_proxy/main.py` — **revert** v1's wiring. Drop the import and the call. `sync_policy_types()` is callable but not invoked from production code paths in this PR. PR #2 (`policy_instance`) wires it in.

This is the mechanism response to devil-2 #7 (preserved from v3, devil-3 acknowledged the trade-off).

## Test changes (v4)

### Unit tests (`tests/luthien_proxy/unit_tests/test_policy_types.py`)

Largely from v1/v2/v3. Updated:

- `test_derive_builtin_name_*` — unchanged
- `test_resolve_collisions_*` — unchanged (semantics noted as display-only in docstring)
- **NEW** `test_registered_builtins_have_no_name_collisions` — run `REGISTERED_BUILTINS` through `resolve_collisions`, assert no `-N` suffixes appear (regression check against today's set)
- **NEW** `test_resolve_description_prefers_attribute_over_docstring` — class with both `__policy_description__` and a docstring; attribute wins
- **NEW** `test_resolve_description_falls_back_to_docstring` — class with only a docstring
- **NEW** `test_resolve_description_returns_none_when_neither` — class with neither

### Integration tests (`tests/luthien_proxy/integration_tests/test_policy_type_sync.py`)

- **CHANGED:** Fixture loads and executes the real SQLite migration file from `src/luthien_proxy/utils/sqlite_migrations/014_add_policy_type.sql`. No hand-rolled DDL. (Devil-1 #9.)
- `test_sync_seeds_policy_types` — verify rows for every entry in `REGISTERED_BUILTINS`, all with `definition_type = 'built-in'` and populated `module_path`
- `test_sync_is_idempotent` — same intent
- `test_sync_marks_missing_classes_as_deprecated` — call sync with full list, then call with shortened list (passed as `class_refs=` parameter); verify dropped class is marked `deprecated=1`
- `test_sync_resurrects_class_when_returned_to_list` — symmetric
- `test_sync_unique_constraint_on_module_path` — attempt to insert two rows with same `module_path`, verify second raises (and propagates — devil-3 #7)
- `test_sync_per_class_failure_does_not_break_loop` — pass `class_refs` containing one bad ref ("nonexistent.module:Foo") and several good ones; verify good ones are registered, bad one is logged + absent
- **NEW** `test_sync_propagates_db_setup_failure` — close the pool before calling sync; verify it raises
- `test_module_path_uniqueness_enforced_by_db` — direct INSERT bypassing sync, verify constraint fires

## Migration handling (v4)

**Edit `014_*` files in place. No 015.** Devil-3 was right: 014 has only been applied to one developer's local DB on a feature branch. "Drop your local DB and re-pull" is a one-liner for the dev; preserving 014 + adding 015 leaves vapor CHECK constraints on disk forever.

Three files updated in place:
- `migrations/postgres/014_add_policy_definition.sql` → renamed to `014_add_policy_type.sql`, content replaced with v4 schema
- `migrations/sqlite/014_add_policy_definition.sql` → renamed to `014_add_policy_type.sql`, content replaced
- `src/luthien_proxy/utils/sqlite_migrations/014_add_policy_definition.sql` → renamed and content replaced

If anyone has applied old-014 to a local SQLite DB, their dev DB will fail the migration hash check on next start. Resolution: delete `~/.luthien/local.db` (the default sqlite path).

## File changes summary

### Renamed (rename + content replace)
- `src/luthien_proxy/policy_definitions.py` → `src/luthien_proxy/policy_types.py`
- `tests/luthien_proxy/unit_tests/test_policy_definitions.py` → `test_policy_types.py`
- `tests/luthien_proxy/integration_tests/test_policy_definition_sync.py` → `test_policy_type_sync.py`
- All three migration files (014_add_policy_definition.sql → 014_add_policy_type.sql)

### Modified
- `src/luthien_proxy/main.py` — revert v1's import + sync call. No production wiring of `sync_policy_types` in this PR.

### NOT modified
- No changes to existing built-in policy classes (no `__policy_registered__` markers needed; allowlist is in `policy_types.py`)
- No changes to `policy_discovery.py` or its `_discovered_policies_cache` (sync no longer uses discovery; allowlist is independent)

## Invariants (v4)

- **Identity for built-in is `(definition_type='built-in', module_path)`**, enforced by partial unique index. Postgres CHECK ensures `module_path NOT NULL` when `definition_type = 'built-in'`.
- **`name` is display-only.** No FK references it. May change across runs; lookups MUST be by `id` or `module_path`, never `name`.
- **`REGISTERED_BUILTINS` in `policy_types.py` is the source of truth** for which built-ins are registered. Adding a new policy = one line in this list. Removing = either delete from the list (sync deprecates the row) or delete the class entirely (sync logs + skips).
- **Sync is loud on DB-level failures, quiet on per-class import failures.** Caught exceptions are exactly `(ImportError, AttributeError, ValueError)`. Database exceptions are not caught.
- **Sync is not wired into `main.py`** in this PR. PR #2 wires it.

## Assumptions (v4)

- The 18-entry allowlist accurately reflects the user-deployable policies as of today. (Verified against `discover_policies()` output.)
- SQLite supports `ON CONFLICT (col) WHERE ... DO UPDATE` (3.35+; we have 3.45+). If the integration test fails on this syntax, fall back to fetch-then-update-or-insert in Python (no schema change required).
- `_translate_params` in `db_sqlite.py` correctly translates `$N` → `?` for the upsert SQL (verified: existing `policy_definitions.py` v1 used the same pattern).
- Editing migration 014 in place is acceptable because it has only been applied to one developer's local SQLite DB on this feature branch. Documented dev-side workaround: delete the local DB.

## Out of scope (v4)

- `policy_instance` table and FK
- `defined-in-db` / `ghref` / `policystore` definition types
- JSONB `definition_ref` column (added with the first non-built-in type that needs it)
- Wiring sync into `main.py` lifespan (PR #2)
- `current_policy` FK
- Hard-delete admin endpoint
- Validating policy constructibility at registration time
- Any change to `policy_discovery.py` or its cache
