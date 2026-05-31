# Objective

Polish follow-ups from PR #606 review for the policy_types module (Trello gw6MJIlG): convert the per-row deprecation loop in `sync_policy_types` to a single batched `UPDATE ... WHERE class_ref NOT IN (...)`. Items 1 (regex split) and 3 (test coupling comment) were already landed in commit 85872f5b on origin/main; only item 2 remains.

## Description

`sync_policy_types` upserts built-in policy_type rows, then deprecates any existing built-in row whose `class_ref` is no longer in the allowlist. The deprecation step currently issues one `UPDATE` per stale row in a Python loop. The DB layer (`db_sqlite._translate_params`) translates asyncpg-style `$N` placeholders to SQLite `?` generically, so a dynamically-built `NOT IN ($1, $2, ...)` placeholder list is portable across both backends.

## Approach

- Replace the per-row deprecation loop with a single batched `UPDATE policy_type SET deprecated = TRUE WHERE definition_type = 'built-in' AND class_ref NOT IN ($1, ..., $N)`.
- Build the placeholder list dynamically from `seen_class_refs`.
- Guard the empty case: `NOT IN ()` is invalid SQL. When `seen_class_refs` is empty, deprecate ALL existing built-in rows (matches old loop behavior — every existing row would be in `to_deprecate`).
- Drop the now-unnecessary `existing` fetch + set-difference; the SQL `NOT IN` does that work.

## Hot-path sequence

```
# Before: N+1 round trips (1 SELECT + one UPDATE per stale row)
SELECT class_ref FROM policy_type WHERE definition_type='built-in'   -> {a,b,c,d}
# seen = {a,b}; to_deprecate = {c,d}
UPDATE policy_type SET deprecated=TRUE WHERE class_ref='c'
UPDATE policy_type SET deprecated=TRUE WHERE class_ref='d'

# After: 1 round trip (seen non-empty)
UPDATE policy_type SET deprecated=TRUE
  WHERE definition_type='built-in' AND class_ref NOT IN ($1,$2)   -- a,b

# After: seen empty (all imports failed) -> deprecate all built-ins
UPDATE policy_type SET deprecated=TRUE WHERE definition_type='built-in'
```

## External Contracts

- `policy_type` table schema: columns `class_ref`, `deprecated`, `definition_type` unchanged. Behavior preserved: a built-in row not in the allowlist ends with `deprecated = TRUE`; a row in the allowlist keeps `deprecated = FALSE` (set by the upsert). No change to the partial unique index.
- asyncpg / SQLite `$N` placeholder contract: dynamically generated `$1..$N` must round-trip through `db_sqlite._translate_params` (it does — generic regex substitution).

## Assumptions

- The upsert step already sets `deprecated = FALSE` for every seen row, so the batched UPDATE only needs to flip stale rows to TRUE (it never needs to un-deprecate). Falsifiable: existing test `test_sync_resurrects_class_when_returned_to_list` exercises resurrection and must still pass.

## Test Strategy

- Existing integration test `test_sync_marks_missing_classes_as_deprecated` proves the batched UPDATE deprecates the dropped class. It would fail if the placeholder list or guard is wrong.
- Add `test_sync_deprecates_all_when_no_classes_seen` (empty-seen guard) to lock the `NOT IN ()` edge case.
- Existing `test_sync_resurrects_class_when_returned_to_list` proves no over-deprecation / correct un-deprecation.
- Run the full unit + sqlite integration suites for the changed module.

## Acceptance Criteria

- [ ] Empty-seen guard test written (fails before the guard exists if implemented naively)
- [ ] Deprecation loop replaced with single batched UPDATE
- [ ] All existing policy_type sync tests pass
- [ ] dev_checks passes for changed files

## Tracking

- Trello: https://trello.com/c/gw6MJIlG
- Branch: polish/policy-types-nits
- PR: https://github.com/LuthienResearch/luthien-proxy/pull/793 (draft)
