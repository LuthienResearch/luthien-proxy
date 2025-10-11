# TODO

## High Priority

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
- [ ] Add dataflows pointer to AI prompts

## Low Priority

- [ ] Add composite DB index for frequent debug log queries [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Configure asyncpg pool command timeout settings [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Apply per-query timeouts to heavy debug log fetches [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Investigate circuit breaker guard for slow database operations [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Extract shared jsonblob parsing into `parse_data.py` for hooks/debug routes
- [ ] Maybe logging for bg tasks [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321954052)
- [ ] Actual type signatures on hook functions in litellm_callback
- [ ] Make callback module independent (include connection manager)
- [ ] Prefer (Type | None) to (Optional\[Type\]) throughout codebase
- [ ] Minimize :  ignore flags
- [x] Extract magic numbers to named constants (chunk preview length=50, poll_interval minimum=0.01) [comment](https://github.com/LuthienResearch/luthien-proxy/pull/28#issuecomment-MULTIPLE)
