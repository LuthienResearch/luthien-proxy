# TODO

Items sourced from PR #46 reviews link back to originating comments for context.

## Critical (Before Merge)

- [ ] Add comprehensive PR description explaining V2 migration and ~9K line cleanup ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Ensure all tests pass (`./scripts/dev_checks.sh`) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [x] Update `CHANGELOG.md` with proper PR reference (link to #46) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Fix request cleanup bug in synchronous_control_plane.py:81 (memory leak on request failure) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add OpenTelemetry span to Anthropic endpoint (/v1/messages) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add production security warnings to .env.example (dev credentials must be changed) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))

## High Priority

- [ ] Add security documentation for dynamic policy loading mechanism (V2_POLICY_CONFIG) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Verify all environment variables are documented in README and .env.example ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Add max buffer size for chunk storage (synchronous_control_plane.py:220 - unbounded growth) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Review and test graceful shutdown behavior (ensure event publisher tasks complete) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add input validation: max request size and message count limits ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Optional -> | None (type annotations cleanup - only 21 Optional[] remaining, 61 already using | None)

## Medium Priority

- [ ] Add rate limiting middleware (slowapi or custom FastAPI middleware) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Implement circuit breaker for upstream calls ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add Prometheus metrics endpoint ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Make streaming timeout configurable (currently hardcoded 30s) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Implement proper task tracking for event publisher (replace fire-and-forget) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add integration tests for error recovery paths ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Factor out env var logic into centralized config
- [ ] Add OpenAPI/Swagger documentation for V2 gateway ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Document production deployment best practices ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Add resource limits to docker-compose.yaml (mem_limit, cpus) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))

## Low Priority / Future Work

- [ ] 99% unit test coverage (currently 81%, focus on critical paths first) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add config schema validation (Pydantic model for v2_config.yaml) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add request/response size limits ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Implement adaptive timeout based on model type ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add policy composition (chaining multiple policies) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Expose database connection pooling configuration (pool size, timeout) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add cache headers to static files mount ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Standardize on docstrings (currently mixed ABOUTME/docstrings) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Extract magic numbers to named constants (timeouts, truncation lengths) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Consider stricter type checking (pyright "standard" or "strict" mode) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add health check endpoint with degraded state reporting ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Minimize type: ignore flags
- [ ] Load testing with multiple concurrent streams ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Memory leak detection for long-running streams ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Redis pub/sub performance testing under high event volume ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
