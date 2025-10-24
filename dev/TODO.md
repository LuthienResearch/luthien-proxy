# TODO

## Critical (Before Merge)

- [ ] Add comprehensive PR description explaining V2 migration and ~9K line cleanup
- [ ] Ensure all tests pass (`./scripts/dev_checks.sh`)
- [ ] Update `CHANGELOG.md` with proper PR reference (link to #46)
- [ ] Clear `dev/OBJECTIVE.md` and `dev/NOTES.md` per workflow

## High Priority

- [x] Update public documentation (docs/) to reflect V2 architecture (DONE: archived V1 docs, updated README/CLAUDE.md/AGENTS.md)
- [x] Remove stale references to deleted V1 modules from all docs (DONE: cleaned context files, e2e tests)
- [ ] Add security documentation for dynamic policy loading mechanism (V2_POLICY_CONFIG)
- [ ] Verify all environment variables are documented in README and .env.example
- [ ] Add migration guide for V1 → V2 (if replacing existing deployment)
- [ ] Address test coverage gaps or document why they're acceptable
- [ ] Optional -> | None (type annotations cleanup)
- [ ] Sweep code for "defensive coding" anti-patterns and remove
- [ ] Make sure tests match package file structure
- [ ] pw-protected UI w/ config
- [ ] Make data logging more efficient

## Medium Priority

- [ ] 99% unit test coverage
- [ ] Factor out env var logic into centralized config
- [ ] Integration test for concurrent streams
- [ ] Consider adding rate limiting to authentication endpoints
- [ ] Add OpenAPI/Swagger documentation for V2 gateway
- [ ] Document production deployment best practices
- [ ] Add health check endpoint with degraded state reporting (e.g., "healthy but observability unavailable")

## Low Priority

- [ ] Minimize type: ignore flags
- [ ] Load testing with multiple concurrent streams
- [ ] Memory leak detection for long-running streams
- [ ] Redis pub/sub performance testing under high event volume
