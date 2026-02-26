## Policy Composition + Dogfood Safety

Replace the hacky approach in PR #243 with a clean, general-purpose policy composition mechanism.

### Acceptance Criteria

1. `compose_policy()` function that inserts a policy into an existing policy chain at a specified position
2. `MultiSerialPolicy.from_instances()` classmethod for building chains from pre-instantiated policies
3. `DogfoodSafetyPolicy` ported from PR #243 (regex-based command blocking)
4. `ENABLE_DOGFOOD_POLICY=true` env var auto-injects dogfood policy via `compose_policy()`
5. Unit tests for all new code
6. `dev_checks.sh` passes
