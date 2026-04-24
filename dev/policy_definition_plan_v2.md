# Policy Definition Table — Plan v2 (post-devil)

## What changed and why

The first devil pass found 10 issues. This plan addresses them. The original plan is at `dev/policy_definition_plan.md`; this supersedes it.

Devil's strongest critique is that **`name` is a load-bearing identity that the design treats as cosmetic**. We were going to FK `policy_instance` on `name`, while a code rename to a third class could silently rewrite `name` for an unrelated existing class. Fix: stable identity is `module_path` (for built-in) / equivalent for other types; `name` becomes a regenerable display field. Everything else flows from that.

Devil's secondary critique is that **auto-discovery sweeps in templates and samples**. `policies/hackathon_policy_template.py` and `policies/sample_pydantic_policy.py` would be registered as deployable types on day one. Fix: explicit opt-out marker on classes that shouldn't appear in the registry.

Two of devil's points get pushback (#3, #7) — covered in the "Pushback" section below. The rest land.

## Decision summary (devil item → response)

| # | Devil's point | Response |
|---|---|---|
| 1 | Collision suffixes unstable across code changes; FK-by-name is broken | **Fix.** Stable identity is `module_path`. `name` is a display field with no FK responsibility. Future `policy_instance` FKs on `policy_definition.id`, not `name`. |
| 2 | Templates/samples auto-registered | **Fix.** Add `__policy_registered__: bool = True` class attribute; sync skips classes that set it to `False`. Mark `HackathonPolicyTemplate`, `SamplePydanticPolicy`, and the hackathon onboarding policy as `False`. |
| 3 | Sync runs with no readers and swallows errors | **Partial fix.** Don't swallow — let sync raise on real DB errors so failures are loud. Per-class import/check failures still log + skip (intentional, so a single bad class doesn't break the whole sync). The "no readers yet" critique is real but addressed in Pushback below. |
| 4 | `_discovered_policies_cache` hidden coupling | **Fix.** Sync takes an explicit `discover` callable parameter (default `discover_policies`). Tests pass their own. Cache stays for the admin endpoint's use, but sync no longer participates in it. |
| 5 | Two sources of truth for description | **Fix.** Sync reads `__policy_description__` if present, falls back to `extract_description(class)` (docstring) if not. Single helper extracted to a shared utility so both sync and admin discovery use the same logic. Admin discovery already uses docstring; this just adds the attribute as an override. |
| 6 | `policystore` CHECK constraint with no semantics | **Fix.** Drop `policystore`, `defined-in-db`, and `ghref` from the CHECK. The constraint allows only `'built-in'` for now. New types are added by future migrations when their semantics are defined. |
| 7 | This PR has no readers; entire thing is scaffolding | **Pushback.** Layered approach is intentional (user explicitly chose it). Mitigations: see Pushback below. |
| 8 | Sync's `BasePolicy` check duplicates discovery; doesn't match PolicyManager's actual load path | **Fix.** Replace `issubclass(BasePolicy)` check with `luthien_proxy.config._import_policy_class(class_ref)` — that's the same import path PolicyManager uses to load. If it doesn't import-and-validate cleanly, the class isn't registered. |
| 9 | Test fixture DDL drifted from real migration (`AUTOINCREMENT`) | **Fix.** Fixture loads and executes the real SQLite migration file at `src/luthien_proxy/utils/sqlite_migrations/014_add_policy_definition.sql`. No hand-rolled DDL. |
| 10 | `policy_definition` name pollution | **Fix.** Rename `policy_definition` → `policy_type` everywhere (table, file, function, tests). Easier now than after `policy_instance` lands and adds FK references. |

## Schema (revised)

```sql
CREATE TABLE policy_type (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,             -- display field; auto-derived; can change across runs
    description     TEXT,
    definition_type TEXT NOT NULL CHECK (definition_type IN ('built-in')),
    definition_ref  JSONB NOT NULL,
    config_schema   JSONB,
    deprecated      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Stable identity for built-in: the class_ref string, extracted from definition_ref.
-- Postgres expression index + uniqueness constraint enforces "one row per built-in class."
CREATE UNIQUE INDEX idx_policy_type_builtin_module_path
    ON policy_type ((definition_ref->>'module_path'))
    WHERE definition_type = 'built-in';

CREATE INDEX idx_policy_type_active
    ON policy_type (definition_type)
    WHERE NOT deprecated;
```

SQLite:
- `JSONB` → `TEXT` (json_extract used for the unique index)
- `BOOLEAN` → `INTEGER`
- `TIMESTAMPTZ` → `TEXT DEFAULT (datetime('now'))`
- Unique index uses `json_extract(definition_ref, '$.module_path')` instead of `->>`. SQLite supports expression indexes since 3.9; we have 3.45+.
- Partial index: `WHERE definition_type = 'built-in'` and `WHERE deprecated = 0`.

## Sync algorithm (revised)

`sync_builtin_policy_types(pool: DatabasePool, *, discover: Callable[[], list[dict]] = discover_policies) -> None`

1. Call `discover()` to get the candidate list.
2. For each entry, attempt to load via `luthien_proxy.config._import_policy_class(class_ref)`. If that raises, log and skip (don't register a class that PolicyManager couldn't load).
3. Skip any class with `__policy_registered__ = False`.
4. Compute `name` via `derive_builtin_name(class_name)`. Resolve display-name collisions deterministically by `class_ref` order, appending `-2`, `-3`. Collisions are display-only (no FK responsibility), so suffix instability across renames is no longer a correctness bug — it's a UX wart we accept.
5. Read `description = getattr(cls, '__policy_description__', None) or extract_description(cls) or None`.
6. Upsert by `(definition_type='built-in', module_path)` (the unique index). On conflict, update `name`, `description`, `deprecated=false`, `updated_at`. Note: `name` *can* change on update (because it's display-only), and that's fine.
7. Mark missing built-ins (rows with module_path not in the current discovery set) as `deprecated=true`. Don't delete.
8. **Don't swallow exceptions at the call site.** If sync raises (DB unreachable, schema wrong, etc.), let it propagate. Per-class failures are caught inside the loop; structural failures fail startup loud.

### `derive_builtin_name` and `resolve_collisions` are unchanged in implementation but their semantics shift:

- They produce display names, not identities.
- `resolve_collisions` no longer needs cross-rename stability. Add a comment noting that.
- The unit tests stand. Add one new test: when discovery's actual current output is run through `resolve_collisions`, no two rows share a name (regression check against today's codebase).

### Class-attribute opt-out

Default: classes are registered.

```python
class HackathonPolicyTemplate(BasePolicy):
    __policy_registered__ = False
    """[describe what it does here]"""
```

Marked classes:
- `policies/hackathon_policy_template.py:HackathonPolicyTemplate`
- `policies/sample_pydantic_policy.py:SamplePydanticPolicy`
- `policies/hackathon_onboarding_policy.py:HackathonOnboardingPolicy` (per its `policies/__init__.py` reference, this is also example/demo code — confirm via inspection)

Anything else with the attribute set to `False` is skipped silently.

## Test changes

- **Replace hand-rolled fixture DDL** with execution of the real migration file. The fixture reads `src/luthien_proxy/utils/sqlite_migrations/014_add_policy_type.sql` (renamed from policy_definition) and runs each statement. The runner already handles this for app startup; tests use the same code path via a small helper.
- **New unit test:** `test_real_discovery_produces_no_name_collisions` — call `discover_policies()` for real, run through `resolve_collisions`, assert no `-2`/`-3` suffixes. Catches accidental collisions in the real built-in set today.
- **New unit test:** `test_unregistered_classes_are_skipped` — fake discovery output containing a class with `__policy_registered__ = False`, verify it's not in the result.
- **New integration test:** `test_per_class_import_failure_is_logged_and_skipped` — patch `_import_policy_class` to raise for one entry, verify other entries still register and the bad one is absent.
- **Updated integration test:** `test_sync_raises_on_db_failure` — provide a closed pool, verify sync raises (replaces the implicit "swallowed via try/except in main.py" behavior). The lifespan in `main.py` no longer wraps in try/except.
- **Drop the redundant `_wrap_sqlite_pool` helper** — already done in v1 cleanup, this just confirms it stays gone.

## Pushback on devil

**#3, in part: "no readers, pure attack surface."** Devil is right that the table has no production readers in this PR. The mitigations:

- We're committed to building `policy_instance` as the immediate next PR (this is the explicit motivator). If that doesn't land within a sprint, this PR should be reverted, not left dangling.
- Replacing the swallowing try/except with raise-on-failure means a broken sync becomes a startup failure visible immediately, not a silent bad row that surfaces months later.
- Sync time cost is bounded: `discover_policies()` is already called by the admin endpoint's first request; this PR moves it earlier (startup). Not new code execution, just earlier.
- Alternative: fold `policy_instance` into this PR. Larger blast radius for review; harder to revert. Recommend against unless the user wants it.

**#7: "this is scaffolding for an unwritten feature."** True. The decision to build the registry first was deliberate — the alternative (build `policy_instance` first with no type registry, then refactor to add one) is harder. The risk of "instance never lands" is real and is mitigated by treating that as a hard commitment, not a "follow-up."

**Devil-suggested change I'm rejecting: "use `module_path` as PK; drop `name` as identity."** Half-adopted. `module_path` is the unique identity (via expression index) for built-ins, but a SERIAL `id` PK is cleaner for FKs from `policy_instance` and for non-built-in types where "module_path" doesn't exist. So: SERIAL PK, with module-path uniqueness enforced for built-ins via partial unique index. This is the strongest version of devil's recommendation that survives multi-type definition_ref shapes.

**Devil-suggested change I'm rejecting: "drop `description` from the schema until you have a source of truth."** Description is a low-cost field with a clear purpose (UI display). Falling back to docstring (devil's #5 fix) means it has a non-NULL value for every existing built-in on day one. Removing the column would be over-correction.

## Out of scope (deferred to future PRs)

- `policy_instance` table and FK to `policy_type.id`
- Adding `'defined-in-db'` / `'ghref'` to the CHECK (each lands with its implementation)
- `current_policy` FK to anything (depends on `policy_instance`)
- Hard-delete admin endpoint
- Backfilling `__policy_description__` on existing policies (but devil's fallback-to-docstring fix means descriptions populate on day one anyway)
- Removing `_discovered_policies_cache` from `policy_discovery.py` (just bypassing it from sync via DI)

## Migration handling

This will likely supersede the existing `014_add_policy_definition.sql` files. Two options:

- **A.** Edit `014_*` in place (the old name) since they haven't been merged or applied anywhere. Cleaner history.
- **B.** Add `015_rename_policy_definition_to_policy_type.sql` that does the rename. Honors the "migrations are immutable once landed" convention even though these haven't shipped.

Recommend **A** since the worktree branch hasn't been pushed and the migration hasn't been applied to any real DB. Confirm with user before editing.

## File changes (estimated)

### Renamed
- `migrations/postgres/014_add_policy_definition.sql` → `014_add_policy_type.sql` (also updated content)
- `migrations/sqlite/014_add_policy_definition.sql` → `014_add_policy_type.sql`
- `src/luthien_proxy/utils/sqlite_migrations/014_add_policy_definition.sql` → `014_add_policy_type.sql`
- `src/luthien_proxy/policy_definitions.py` → `policy_types.py`
- `tests/luthien_proxy/unit_tests/test_policy_definitions.py` → `test_policy_types.py`
- `tests/luthien_proxy/integration_tests/test_policy_definition_sync.py` → `test_policy_type_sync.py`

### Modified
- `src/luthien_proxy/main.py` — drop the try/except wrapper around sync; update import name
- All three migration files (DDL changes for unique index, name field semantics, single-value CHECK)
- `src/luthien_proxy/policy_types.py` — discover param, attr-opt-out, docstring fallback, replace BasePolicy check with `_import_policy_class` round-trip
- `src/luthien_proxy/policies/hackathon_policy_template.py` — add `__policy_registered__ = False`
- `src/luthien_proxy/policies/sample_pydantic_policy.py` — add `__policy_registered__ = False`
- Possibly `src/luthien_proxy/policies/hackathon_onboarding_policy.py` (TBD after inspection)
- Test files — fixture loads real migration; new tests per "Test changes" above

## Invariants (revised)

- Stable identity for built-in: the value of `definition_ref->>'module_path'`, enforced unique via partial index.
- `name` is display-only, no FK target. Allowed to change across syncs.
- Future `policy_instance` FKs on `policy_type.id`, not `name`.
- A row exists if and only if `_import_policy_class(class_ref)` succeeds at sync time AND `__policy_registered__` is not `False`.
- Sync is loud-on-structural-failure, quiet-on-per-class-failure (logs + skips).

## Assumptions (revised)

- `_import_policy_class` is the right contract for "loadable as a policy" (matches PolicyManager's path).
- Adding `__policy_registered__` to template/sample classes is acceptable — no consumer of those classes today reads the attribute, so adding it is non-breaking.
- SQLite's expression index on `json_extract(...)` works in 3.45+ (verify; if not, fall back to a Python-side uniqueness check).
- We are committed to building `policy_instance` next. If not, this PR should be reverted rather than left as dead code.
