# TODO

## v2

- [ ] Optional -> | None
- [ ] Refactor ControlPlaneLocal
- [ ] Sweep code for "defensive coding" anti-patterns and remove
- [ ] Rename, reorganize, and refactor things for maximum legibility
- [ ] Make sure tests match package file structure

## High Priority

- [ ] pw-protected UI w/ config
- [ ] Demo: DB drop
- [ ] Make data logging more efficient
- [ ] Unify response format passed to callbacks *(see dev/unified_callback_migration.md)*
- [ ] Rebuild policy action dashboard on top of structured `policy_events` data
- [ ] Implement in-flight request modification in pre-call hook (currently fire-and-forget)

## Medium Priority

- [ ] 99% unit test coverage
- [x] Why does the control plane docker container take ~5 seconds to restart?
- [ ] Simplify/reduce data passed from litellm to control plane
- [x] Add CI/CD step that runs Prisma migration validation for both control-plane and LiteLLM schemas
- [ ] Event logging architecture indexed by call_id, trace_id to replace the current debug logs system
- [ ] OpenTelemetry/Grafana/Loki for instrumentation/logging/debugging
- [ ] Document all env vars
- [ ] Integration test for concurrent streams against control plane

## Low Priority

- [ ] Minimize :  ignore flags
