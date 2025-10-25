# TODO

## Critical (Before Merge)

- [ ] Add comprehensive PR description explaining V2 migration and ~9K line cleanup
- [ ] Ensure all tests pass (`./scripts/dev_checks.sh`)
- [ ] Update `CHANGELOG.md` with proper PR reference (link to #46)
- [ ] Fix request cleanup bug in synchronous_control_plane.py:81 (memory leak on request failure)
- [ ] Add OpenTelemetry span to Anthropic endpoint (/v1/messages)
- [ ] Add production security warnings to .env.example (dev credentials must be changed)

## High Priority

- [ ] Add security documentation for dynamic policy loading mechanism (V2_POLICY_CONFIG)
- [ ] Verify all environment variables are documented in README and .env.example
- [ ] Add max buffer size for chunk storage (synchronous_control_plane.py:220 - unbounded growth)
- [ ] Review and test graceful shutdown behavior (ensure event publisher tasks complete)
- [ ] Add input validation: max request size and message count limits
- [ ] Optional -> | None (type annotations cleanup - only 21 Optional[] remaining, 61 already using | None)

## Medium Priority

- [ ] Add rate limiting middleware (slowapi or custom FastAPI middleware)
- [ ] Implement circuit breaker for upstream calls
- [ ] Add Prometheus metrics endpoint
- [ ] Make streaming timeout configurable (currently hardcoded 30s)
- [ ] Implement proper task tracking for event publisher (replace fire-and-forget)
- [ ] Add integration tests for error recovery paths
- [ ] Factor out env var logic into centralized config
- [ ] Add OpenAPI/Swagger documentation for V2 gateway
- [ ] Document production deployment best practices
- [ ] Add resource limits to docker-compose.yaml (mem_limit, cpus)

## Low Priority / Future Work

- [ ] 99% unit test coverage (currently 81%, focus on critical paths first)
- [ ] Add config schema validation (Pydantic model for v2_config.yaml)
- [ ] Add request/response size limits
- [ ] Implement adaptive timeout based on model type
- [ ] Add policy composition (chaining multiple policies)
- [ ] Expose database connection pooling configuration (pool size, timeout)
- [ ] Add cache headers to static files mount
- [ ] Standardize on docstrings (currently mixed ABOUTME/docstrings)
- [ ] Extract magic numbers to named constants (timeouts, truncation lengths)
- [ ] Consider stricter type checking (pyright "standard" or "strict" mode)
- [ ] Add health check endpoint with degraded state reporting
- [ ] Minimize type: ignore flags
- [ ] Load testing with multiple concurrent streams
- [ ] Memory leak detection for long-running streams
- [ ] Redis pub/sub performance testing under high event volume
