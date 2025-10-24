# TODO

## Critical (Before Merge)

- [ ] Add comprehensive PR description explaining V2 migration and ~9K line cleanup
- [ ] Ensure all tests pass (`./scripts/dev_checks.sh`)
- [ ] Update `CHANGELOG.md` with proper PR reference (link to #46)

## High Priority

- [ ] Add security documentation for dynamic policy loading mechanism (V2_POLICY_CONFIG)
- [ ] Verify all environment variables are documented in README and .env.example
- [ ] Address test coverage gaps or document why they're acceptable
- [ ] Optional -> | None (type annotations cleanup - only 21 Optional[] remaining, 61 already using | None)

## Medium Priority

- [ ] Factor out env var logic into centralized config
- [ ] Integration test for concurrent streams
- [ ] Add OpenAPI/Swagger documentation for V2 gateway
- [ ] Document production deployment best practices

## Low Priority / Future Work

- [ ] 99% unit test coverage (currently 81%, focus on critical paths first)
- [ ] Consider adding rate limiting to authentication endpoints
- [ ] Add health check endpoint with degraded state reporting (e.g., "healthy but observability unavailable")
- [ ] Minimize type: ignore flags
- [ ] Load testing with multiple concurrent streams
- [ ] Memory leak detection for long-running streams
- [ ] Redis pub/sub performance testing under high event volume
