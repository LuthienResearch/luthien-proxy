# Policy Type Table — Plan v3 (post-devil-2)

Supersedes `dev/policy_definition_plan_v2.md`. Devil-2 endorsed v2 with 7 required changes; this plan addresses them. Original plan: `dev/policy_definition_plan.md`.

## Devil-2 punch list → response

| # | Devil-2 required change | Response |
|---|---|---|
| 1 | Plan named non-existent class (`HackathonPolicyTemplate` instead of real `HackathonPolicy`) | **Fix.** Verified actual class names by reading source: `HackathonPolicy` (`hackathon_policy_template.py:26`), `SamplePydanticPolicy` (`sample_pydantic_policy.py:48`), `HackathonOnboardingPolicy` (`hackathon_onboarding_policy.py:64`). Used as the canonical names below. Also see #4 — opt-in flips the failure mode so a misnamed class is silently skipped instead of silently registered. |
| 2 | `_import_policy_class` does NOT match PolicyManager's load path (it's importable-symbol only) — swap is cosmetic | **Fix.** Drop the swap. Keep the existing inline `importlib + issubclass(BasePolicy)` check. Document explicitly: we are NOT validating constructibility because instantiation can have side effects (DB connections, env requirements). Validation that the class loads as the *active* policy is PolicyManager's job at activation time, not ours at registration time. A class can be registered here and still fail to activate; that's a UX problem solved at activation, not at registration. |
| 3 | Unique partial index `((definition_ref->>'module_path'))` does not enforce shape; NULL collisions allowed; PG/SQLite type-confusion divergence | **Fix.** Promote `module_path` to a real column. NOT NULL for built-in (enforced via partial CHECK). Unique constraint on `module_path WHERE definition_type = 'built-in'`. `definition_ref` JSONB stays for type-specific extras (currently empty `{}` for built-in; ghref/etc. will populate). |
| 4 | `__policy_registered__` opt-out has no fail-safe — forgetting the marker silently registers templates/samples; class-vs-instance attribute ambiguity | **Fix.** Flip to opt-in: `__policy_registered__ = True` is required for sync to register a class. Default behavior (no attribute) is "skip." Add filename-pattern backstop: skip files matching `*template*.py`, `sample_*.py`, `*_example.py` even if they have the attribute. Belt + suspenders. Apply via `getattr(cls, '__policy_registered__', False)` so instance-level attribute leaks aren't visible. |
| 5 | "Edit migration 014 in place" is intent-not-mechanism if any dev has applied 014 locally | **Fix.** Bump to migration 015. Keep 014 (it created the soon-defunct `policy_definition` table; rolling it back is a real schema change, not a doc change). New 015 migration: `DROP TABLE IF EXISTS policy_definition; CREATE TABLE policy_type (...)`. Migration history stays clean; dev DBs that already ran 014 get the rollback for free. |
| 6 | Loud-on-startup-failure can brick gateway on row-write failure unrelated to schema | **Fix.** Two-tier exception handling. **Per-class write failures inside the loop**: catch + log + skip (one bad class doesn't kill the sync). **Setup-level failures** (table missing, DB unreachable, transaction can't open): propagate and fail startup. Concretely: wrap the body of the for-loop in try/except, but no try/except around the outer transaction or the `pool.connection()` acquisition. |
| 7 | "We commit to building policy_instance next" is intent-not-mechanism — pushback #7 stays weak | **Fix.** Don't wire sync into `main.py` at all. The `sync_policy_types()` function is added but uncalled in production code paths. PR #2 (`policy_instance`) is the one that wires it in (either at startup or lazily on first admin read — that's a PR #2 design call). If `policy_instance` never lands, this PR ships dead-but-callable code with zero startup cost and no brick-the-gateway risk. Mechanism, not intention. |

## Bonus from devil-2

> "API-level lookups MUST be by `id`; `name` is for display only" — add to invariants.

Adopted. Sync code MUST upsert by `module_path` not `name`. PR #2 MUST FK on `id`. Any future code that queries `WHERE name = ...` is a bug.

## Schema (v3)

### Postgres (`migrations/postgres/015_replace_policy_definition_with_policy_type.sql`)

```sql
-- ABOUTME: Replace migration 014's policy_definition table with policy_type
-- ABOUTME: New shape: module_path is a real column with proper unique constraint; CHECK accepts only 'built-in' for now

DROP TABLE IF EXISTS policy_definition;

CREATE TABLE policy_type (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    definition_type TEXT NOT NULL CHECK (definition_type IN ('built-in')),
    module_path     TEXT,                                  -- NOT NULL for built-in (enforced below)
    definition_ref  JSONB NOT NULL DEFAULT '{}'::jsonb,    -- type-specific extras; empty for built-in today
    config_schema   JSONB,                                 -- NULL for built-in (derived at runtime)
    deprecated      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT policy_type_builtin_module_path_required
        CHECK (definition_type <> 'built-in' OR module_path IS NOT NULL)
);

CREATE UNIQUE INDEX idx_policy_type_builtin_module_path
    ON policy_type (module_path)
    WHERE definition_type = 'built-in';

CREATE INDEX idx_policy_type_active
    ON policy_type (definition_type)
    WHERE NOT deprecated;
```

### SQLite (`migrations/sqlite/015_*` and the runtime copy)

Type translations per `migrations/AGENTS.md`:
- `SERIAL` → `INTEGER PRIMARY KEY`
- `JSONB` → `TEXT`
- `BOOLEAN` → `INTEGER`
- `TIMESTAMPTZ` → `TEXT DEFAULT (datetime('now'))`
- `'{}'::jsonb` → `'{}'`
- CHECK constraint syntax is the same.
- Partial indexes are supported in SQLite 3.8+; we have 3.45+.

## Sync algorithm (v3)

`async def sync_policy_types(pool: DatabasePool, *, discover: Callable[[], list[dict]] = discover_policies) -> None`

```
discovered = discover()
candidates = [entry for entry in discovered if _should_register(entry)]
resolved = resolve_collisions(candidates)
seen_module_paths: list[str] = []

async with pool.connection() as conn:                # raises propagate (setup failure → startup fail)
    for assigned_name, entry in resolved:
        try:
            class_ref = entry["class_ref"]
            module_path, class_name = class_ref.split(":", 1)
            module = importlib.import_module(module_path)
            policy_class = getattr(module, class_name)

            if not (isinstance(policy_class, type) and issubclass(policy_class, BasePolicy)):
                logger.warning(f"Skipping {class_ref}: not a BasePolicy subclass")
                continue

            description = (
                getattr(policy_class, '__policy_description__', None)
                or extract_description(policy_class)
                or None
            )

            await conn.execute(
                """
                INSERT INTO policy_type
                    (name, description, definition_type, module_path, definition_ref,
                     config_schema, deprecated, updated_at)
                VALUES ($1, $2, 'built-in', $3, $4, $5, $6, $7)
                ON CONFLICT (module_path) WHERE definition_type = 'built-in' DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    definition_ref = EXCLUDED.definition_ref,
                    deprecated = EXCLUDED.deprecated,
                    updated_at = EXCLUDED.updated_at
                """,
                assigned_name, description, class_ref, "{}", None, False, now_iso,
            )
            seen_module_paths.append(class_ref)
        except Exception as exc:                      # per-class containment
            logger.warning(f"Skipping {entry.get('class_ref', '<unknown>')}: {exc}", exc_info=True)
            continue

    # Mark missing built-ins as deprecated (Python-side set diff for DB portability)
    existing = await conn.fetch(
        "SELECT module_path FROM policy_type WHERE definition_type = 'built-in'"
    )
    existing_paths = {row["module_path"] for row in existing}
    to_deprecate = existing_paths - set(seen_module_paths)
    for path in to_deprecate:
        await conn.execute(
            "UPDATE policy_type SET deprecated = $1 WHERE module_path = $2",
            True, path,
        )
```

### Helper: `_should_register(entry: dict) -> bool`

```
EXCLUDED_FILENAME_PATTERNS = (
    re.compile(r"_template\.py$|_template_"),
    re.compile(r"^sample_|_sample\.py$|_sample_"),
    re.compile(r"^example_|_example\.py$"),
)

def _should_register(entry: dict) -> bool:
    class_ref = entry["class_ref"]
    module_path, _ = class_ref.split(":", 1)

    # Filename backstop
    module_filename = module_path.rsplit(".", 1)[-1] + ".py"
    for pattern in EXCLUDED_FILENAME_PATTERNS:
        if pattern.search(module_filename):
            return False

    # Opt-in attribute (default: don't register)
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, class_ref.split(":", 1)[1])
    except Exception:
        return False
    return getattr(cls, "__policy_registered__", False) is True
```

### Per-built-in opt-in markers

Every existing built-in we want in the registry needs `__policy_registered__ = True`. Survey of `src/luthien_proxy/policies/`:

**Mark `True` (real, deployable policies):**
- `noop_policy.py:NoOpPolicy`
- `simple_noop_policy.py:SimpleNoOpPolicy`
- `simple_policy.py:SimplePolicy`
- `simple_llm_policy.py:SimpleLLMPolicy`
- `all_caps_policy.py:AllCapsPolicy`
- `tool_call_judge_policy.py:ToolCallJudgePolicy`
- `string_replacement_policy.py:StringReplacementPolicy`
- `multi_serial_policy.py:MultiSerialPolicy`
- `conversation_link_policy.py` (whatever class is inside — to be confirmed during implementation)
- `debug_logging_policy.py` (ditto)
- `dogfood_safety_policy.py` (ditto)
- `onboarding_policy.py:OnboardingPolicy`

**Skip (templates/samples/demos — devil-2's #1):**
- `hackathon_policy_template.py:HackathonPolicy` — also caught by `_template` filename pattern
- `sample_pydantic_policy.py:SamplePydanticPolicy` — also caught by `sample_` pattern
- `hackathon_onboarding_policy.py:HackathonOnboardingPolicy` — needs explicit skip OR a filename rename. Filename `hackathon_onboarding_policy.py` doesn't match the patterns. **Decision: just don't add the opt-in marker — opt-in default-false handles it.**

The opt-in default = `False` and the filename patterns are belt-and-suspenders; either one catches templates/samples. New templates added in the future without the `__policy_registered__ = True` marker are correctly skipped.

## Lifespan wiring (v3)

`src/luthien_proxy/main.py` — **no changes.** `sync_policy_types` is not called from production code paths in this PR. The function exists, has tests, and is callable; it's wired in by the future `policy_instance` PR.

This is the mechanism answer to devil-2's #7. If `policy_instance` never lands, this PR ships zero runtime cost.

## Test changes (v3)

### Unit tests

Largely unchanged from v2; rename file `test_policy_types.py`:

- `test_derive_builtin_name_*` — same as v1/v2
- `test_resolve_collisions_*` — same as v1/v2 (semantics now display-only; add comment in test docstrings noting names are *not* identity)
- **NEW** `test_real_discovery_passes_resolve_collisions_cleanly` — call real `discover_policies()`, filter by `_should_register`, run through `resolve_collisions`, assert no `-N` suffixes appear (regression check against today's built-ins)
- **NEW** `test_should_register_skips_classes_without_attribute` — synthetic class lacking `__policy_registered__` is skipped
- **NEW** `test_should_register_skips_filename_patterns` — synthetic discovery entry pointing at a `*_template.py` module is skipped even with attribute set
- **NEW** `test_should_register_accepts_class_with_explicit_true` — class with `__policy_registered__ = True` is registered
- **NEW** `test_should_register_ignores_instance_attribute` — class without the class-level attribute, but with an instance attribute set to True, is skipped (defensive)

### Integration tests

Renamed `test_policy_type_sync.py`:

- **CHANGED:** Fixture executes the real SQLite migration file (loads `src/luthien_proxy/utils/sqlite_migrations/015_replace_policy_definition_with_policy_type.sql` and runs each statement). No hand-rolled DDL. Devil-2 #9.
- `test_sync_seeds_policy_types` — like v2 but verifies `module_path` column populated, `definition_type = 'built-in'`, `name` is unique
- `test_sync_is_idempotent` — same intent
- `test_sync_deprecates_missing_builtins` — same
- `test_sync_resurrects_returning_builtin` — same
- **NEW:** `test_sync_skips_unregistered_classes` — patch discovery to return both an opted-in class and an opted-out class; verify only the opted-in is in the table
- **NEW:** `test_sync_per_class_failure_does_not_break_sync` — patch one entry to raise on import; verify other entries still register and the bad one is absent
- **NEW:** `test_sync_raises_on_db_setup_failure` — provide a closed pool (or otherwise broken setup); verify sync raises (instead of silently swallowing)
- **CHANGED:** Drop `test_description_pulled_from_attribute` in favor of `test_description_falls_back_to_docstring_when_attribute_absent` — test the cascade `__policy_description__ → docstring → None`

## Migration handling (v3, devil-2 #5)

**Two migrations stay in the worktree:**

- `014_add_policy_definition.sql` — unchanged. Created the soon-defunct table.
- `015_replace_policy_definition_with_policy_type.sql` — drops `policy_definition`, creates `policy_type`.

For Postgres + SQLite + runtime-copy, six new files. Verified against `migrations/AGENTS.md` rules.

This is more migration churn than "edit 014 in place" but cleanly handles dev DBs that already ran 014. The diff is committed; nothing depends on the intermediate state.

## File changes summary

### Renamed / replaced
- `src/luthien_proxy/policy_definitions.py` → `src/luthien_proxy/policy_types.py`
- `tests/luthien_proxy/unit_tests/test_policy_definitions.py` → `tests/.../test_policy_types.py`
- `tests/luthien_proxy/integration_tests/test_policy_definition_sync.py` → `tests/.../test_policy_type_sync.py`

### New migrations
- `migrations/postgres/015_replace_policy_definition_with_policy_type.sql`
- `migrations/sqlite/015_replace_policy_definition_with_policy_type.sql`
- `src/luthien_proxy/utils/sqlite_migrations/015_replace_policy_definition_with_policy_type.sql`

### Modified
- `src/luthien_proxy/main.py` — **revert** the v1 wiring (drop the `sync_builtin_policy_definitions` import and call). No production use of sync in this PR.
- ~12 built-in policy files — add `__policy_registered__ = True` class attribute. Tedious but mechanical.
- `src/luthien_proxy/policy_types.py` — full rewrite per algorithm above (DI for discover, opt-in helper, per-class try/except, docstring fallback, no `_import_policy_class` swap, `module_path` column write)

### Tests
- New unit + integration tests per "Test changes" above
- Fixture loads real migration file

## Invariants (v3)

- **Identity for built-in is `(definition_type='built-in', module_path)`**, enforced by partial unique index. `module_path` is the actual stored class_ref string.
- **`name` is display-only.** No FK references it. Allowed to change across runs.
- **Lookups MUST be by `id`** (when FK'd from future tables) **or by `module_path`** (when looking up "did this class get registered"). Lookups by `name` are bugs.
- **`__policy_registered__ = True` is required** for sync to register a class. Default false.
- **Filename patterns** (`*template*`, `sample_*`, `*_example*`) are a backstop — files matching are skipped even with the attribute set.
- **Sync is loud on setup failure, quiet on per-class failure.** No try/except around `pool.connection()` or transaction; per-row writes inside try/except.
- **Sync is not wired into `main.py`** in this PR. PR #2 (`policy_instance`) wires it in.

## Assumptions (v3)

- The real `discover_policies()` output, after filtering by `_should_register`, contains no name collisions with today's built-ins. (Verified by `test_real_discovery_passes_resolve_collisions_cleanly`.)
- Adding `__policy_registered__ = True` to ~12 built-in classes is non-breaking. The attribute has no other consumers in the codebase.
- SQLite partial indexes work for our use (verified — supported since 3.8, we have 3.45+).
- Migration `015` rolls back `014` cleanly; dev DBs that ran `014` will end up with `policy_type` and no `policy_definition` after applying `015`.
- We are NOT committed to building `policy_instance` next. If it never lands, this PR ships dead-but-callable code with no production cost. (This is the mechanism response to devil-2 #7.)

## Out of scope (deferred)

- `policy_instance` table and FK to `policy_type.id`
- Adding `defined-in-db` / `ghref` / `policystore` to the CHECK constraint (each lands with its implementation)
- Wiring `sync_policy_types` into startup or lazy-on-first-read (PR #2's call)
- `current_policy` FK to anything
- Hard-delete admin endpoint
- Validating policy constructibility at registration time (PolicyManager's job at activation, not ours at registration)
- Removing `_discovered_policies_cache` from `policy_discovery.py` (DI bypasses it for sync; admin endpoint keeps it)

## Pushbacks I'm preserving from v2

- **`name` field is kept** despite being display-only. Removing it would lose UI affordance; keeping it adds a column that never participates in any FK. Fine.
- **Description column kept** with cascade source (attribute → docstring → NULL). Devil-2 didn't object; preserved from v2.
