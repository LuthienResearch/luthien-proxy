# TODO

## High Priority

### Core Features (User Story Aligned)

- [x] **thinking and verbosity model flags not respected** - Model parameters like `thinking` and `verbosity` are not passed through to backend. Fixed by preserving extra params in `anthropic_to_openai_request`.

### Policy UI & Admin

- [ ] **Improved policy config schema system** - Enhance config schema to include: default values, per-field user-facing docstrings/descriptions, and secure field flags (e.g. API keys should render as password inputs in browser). Currently the UI infers types from schema but lacks rich metadata for good UX.
- [ ] **[Future] Smart dev key hint** - Only show clickable dev key hint when ADMIN_API_KEY matches default; otherwise just show "check .env or contact admin". Deferred as scope creep. Reference: dogfooding-login-ui-quick-fixes branch, 2025-12-15.
- [ ] **Activity Monitor missing auth indicator** - Gateway root page links to Activity Monitor but doesn't show "Auth Required" indicator for consistency with other protected pages. Reference: dogfooding session 2025-12-15.

### Architecture Improvements

- [x] **create_app dependency injection** - Accept db and redis objects instead of URLs, enabling easier testing and more flexible configuration

### Type System Improvements

- [x] **Break up llm/types.py into submodules** - Split into `llm/types/openai.py` and `llm/types/anthropic.py` for cleaner organization as the file grows.
- [x] **Complete strict typing for LLM types** - Add remaining TypedDict definitions for full request/response typing (OpenAIRequestDict, AnthropicRequestDict, AnthropicResponseDict, tool types).
- [x] **Move Request class to llm/types/openai.py** - Moved from top-level `messages.py` to live with other LLM types.

### Multimodal / Images

- [x] **LiteLLM multimodal routing issue (#108)** - Fixed. Images now pass through correctly.

### Documentation (High)

- [x] Update README post v2-migration
- [ ] **Add security documentation for dynamic policy loading (POLICY_CONFIG)** - Document security implications of dynamic class loading, file permissions, admin API authentication requirements.
- [x] Verify all environment variables are documented in README and .env.example

### Security

- [ ] **Add input validation: max request size and message count limits** - Request size limit (10MB) exists, but no message count limit. Could allow unbounded message arrays.

## Medium Priority

### Code Improvements

- [ ] **SimplePolicy image support** - Add support for requests containing images in SimplePolicy. Currently `simple_on_request` receives text content only; needs to handle multimodal content blocks. (Niche use case - images pass through proxy correctly already)

- [ ] **Replace dict[str, Any] with ToolCallStreamBlock in ToolCallJudgePolicy** - Improve type safety for buffered tool calls
- [ ] **Policy API: Prevent common streaming mistakes** - Better base class defaults and helper functions
- [ ] **Format blocked messages for readability** - Pretty-print JSON, proper line breaks
- [ ] **Improve error handling for OpenTelemetry spans** - Add defensive checks when OTEL not configured (partial: `is_recording()` checks exist)

### Testing (Medium)

- [ ] **Add integration tests for error recovery paths** - DB failures, Redis failures, policy timeouts, network failures
- [ ] **Audit tests for unjustified conditional logic**

### Infrastructure (Medium)

- [x] **Add migration tracking** - Implemented fail-fast validation in run-migrations.sh and gateway startup check. (bd: luthien-proxy-17j, PR #110)
- [ ] **Verify UI monitoring endpoints functionality** - Test all debug and activity endpoints (debug endpoints have tests, UI routes do not)
- [ ] **Add rate limiting middleware** - Not blocking any user story, but useful for production
- [ ] **Implement circuit breaker for upstream calls** - Queue overflow protection exists, but not full circuit breaker pattern
- [x] **Add resource limits to docker-compose.yaml**
- [x] **Review LiteLLMClient instantiation pattern** - Already singleton in main.py:104-105
- [x] **Implement proper task tracking for event publisher** - Has `add_done_callback` for error logging in emitter.py

### Documentation (Medium)

- [ ] Add OpenAPI/Swagger documentation for V2 gateway
- [ ] Document production deployment best practices
- [ ] Document timeout configuration rationale
- [ ] Document data retention policy

### Story 6 (Taylor/Junior Dev) Follow-ups

- [ ] **Find better home for Scott's session debug CSVs** - Currently in `dev/debug_data/` in main repo. Options: separate repo, gist, Google Drive, or gitignore and keep local-only. These are useful for retrospectives but clutter the main repo. Reference: 2025-12-16 dogfooding session.

Future documentation to add as the story matures:

- [ ] **Success metrics** - How do we know it's working? (warning rate decreases, review time decreases). Reference: [Story 6](dev/user-stories/06-junior-developer-learning-with-guardrails.md)
- [ ] **Edge cases** - Long sessions (100+ messages), multiple warnings, Morgan unavailable
- [ ] **Comparison to alternatives** - Why Luthien vs GitHub PR comments alone vs pair programming?
- [ ] **Failure modes** - What if Luthien is down? What if logs are too noisy?
- [ ] **Privacy considerations** - Who can see session logs? Retention policy?

## Low Priority / Future Work

- [ ] **Simplify db.py abstractions** - Remove redundant protocol wrappers
- [ ] **Review observability stack** - Consolidate observability docs, verify Grafana/Loki integration
- [ ] Increase unit test coverage (currently ~90%, target 95%+)
- [ ] Add config schema validation (Pydantic model for policy_config.yaml)
- [ ] Implement adaptive timeout based on model type
- [ ] Add policy composition (chaining multiple policies)
- [ ] Expose database connection pooling configuration
- [ ] Add cache headers to static files mount
- [ ] Consider stricter pyright mode
- [ ] Add degraded state reporting to /health endpoint
- [ ] Minimize type: ignore flags
- [ ] Load testing with multiple concurrent streams
- [ ] Memory leak detection for long-running streams
- [ ] Redis pub/sub performance testing under high event volume
