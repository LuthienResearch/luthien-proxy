# TODO

## High Priority

### Testing

### Policy UI & Admin

- [ ] **Policy Config UI - Connect to Backend** - Wire up the Policy Configuration UI to use real admin API:
  - Update `policy_config.js` to call `/admin/policy/enable` instead of mocking
  - Add admin key input/storage (prompt on first use, store in sessionStorage)
  - Fetch current policy from `/admin/policy/current` on page load
  - Connect SSE to real `/activity/stream` for test detection
- [ ] **Policy Discovery/Listing** - Implement `/admin/policy/list` endpoint with policy metadata
- [ ] **Make policy selection easier for e2e testing** - Allow temporary policy specification without modifying config files

### Architecture Improvements

- [ ] **create_app dependency injection** - Accept db and redis objects instead of URLs, enabling easier testing and more flexible configuration
- [ ] **Cleaner policy config story** - Consolidate policy configuration approach (config file vs db vs runtime injection) with clear precedence rules
- [ ] **Cleaner authentication story** - Unified auth system for API key + general authentication for sensitive endpoints (admin, debug, etc.)

### Infrastructure (High)

- [ ] **Run Database Migration** - Apply `migrations/001_add_policy_config_table.sql`
- [ ] **Remove Prisma from codebase** - Conflicts with SQL migrations:
  - Remove `prisma/` directory
  - Remove `db-migrations` service from docker-compose.yaml
  - Remove prisma from pyproject.toml dependencies
  - Remove prisma migrate steps from CI

### Code Quality

- [ ] **Fix assertion usage in production code** - Replace `assert` statements in `simple_policy.py` with proper exceptions
- [ ] **Replace magic numbers with named constants** - queue_size, max_chunks_queued, truncation lengths
- [ ] **Factor out common gateway route logic** - Extract duplicate pipeline setup from `/v1/chat/completions` and `/v1/messages`
- [ ] **Factor out env var logic into centralized config**

### Documentation (High)

- [ ] Update README post v2-migration
- [ ] Add security documentation for dynamic policy loading (POLICY_CONFIG)
- [ ] Verify all environment variables are documented in README and .env.example

### Security

- [ ] Add input validation: max request size and message count limits

## Medium Priority

### Code Improvements

- [ ] **Replace dict[str, Any] with ToolCallStreamBlock in ToolCallJudgePolicy** - Improve type safety
- [ ] **Policy API: Prevent common streaming mistakes** - Better base class defaults and helper functions
- [ ] **Format blocked messages for readability** - Pretty-print JSON, proper line breaks
- [ ] **Improve error handling for OpenTelemetry spans** - Add defensive checks when OTEL not configured
- [ ] **Review LiteLLMClient instantiation pattern** - Consider singleton instead of per-request
- [ ] thinking and verbosity model flags not respected

### Testing (Medium)

- [ ] **Add integration tests for error recovery paths** - DB failures, Redis failures, policy timeouts, network failures
- [ ] **Convert Loki validation scripts to e2e tests**
- [ ] **Audit tests for unjustified conditional logic**

### Infrastructure (Medium)

- [ ] **DB Migration: call_id -> transaction_id** - Rename columns for consistency
- [ ] **Verify UI monitoring endpoints functionality** - Test all debug and activity endpoints
- [ ] Add rate limiting middleware
- [ ] Implement circuit breaker for upstream calls
- [ ] Add Prometheus metrics endpoint
- [ ] Implement proper task tracking for event publisher (replace fire-and-forget)
- [ ] Add resource limits to docker-compose.yaml

### Documentation (Medium)

- [ ] Add OpenAPI/Swagger documentation for V2 gateway
- [ ] Document production deployment best practices
- [ ] Document timeout configuration rationale
- [ ] Document data retention policy

## Low Priority / Future Work

- [ ] **Simplify db.py abstractions** - Remove redundant protocol wrappers
- [ ] **Review observability stack** - Consolidate observability docs, verify Grafana/Loki integration
- [ ] Increase unit test coverage (currently ~78%)
- [ ] Add config schema validation (Pydantic model for policy_config.yaml)
- [ ] Implement adaptive timeout based on model type
- [ ] Add policy composition (chaining multiple policies)
- [ ] Expose database connection pooling configuration
- [ ] Add cache headers to static files mount
- [ ] Consider stricter pyright mode
- [ ] Add health check endpoint with degraded state reporting
- [ ] Minimize type: ignore flags
- [ ] Load testing with multiple concurrent streams
- [ ] Memory leak detection for long-running streams
- [ ] Redis pub/sub performance testing under high event volume
