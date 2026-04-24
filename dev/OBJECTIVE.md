# Objective: Add `policy_type` table

Registry of available policy types (built-in for now), decoupled from "which policy is currently active." First of two layered tables; future `policy_instance` PR will FK into this one.

See `dev/policy_definition_plan_v5.md` for the canonical plan (referenced devil rounds: v1 → v2 → v3 → v4 → v5).

## Acceptance Check

- `policy_type` table exists in Postgres (migration 015) and SQLite (mirrored + runtime copy).
- `sync_policy_types()` is implemented and tested but **not** wired into `main.py` lifespan — PR #2 wires it in.
- 18-entry explicit `REGISTERED_BUILTINS` allowlist in `policy_types.py` (3 templates/samples skipped).
- Stable identity is `(definition_type='built-in', module_path)` enforced by partial unique index. `name` is display-only.
- Unit (17) and integration (8) tests green. `dev_checks.sh` clean.
