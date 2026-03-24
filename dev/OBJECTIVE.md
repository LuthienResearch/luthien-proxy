# Objective: Unify Policy Interface to Hooks-Only

## PR 1: Drop MultiParallelPolicy

Remove MultiParallelPolicy — it's unused and is the only policy that requires `run_anthropic`'s full power (multiple backend calls). Removing it unblocks simplifying the policy interface to hooks-only.

### Acceptance Criteria

- MultiParallelPolicy source, tests, and all references deleted
- `dev_checks.sh` passes
- No remaining imports/references to MultiParallelPolicy
