# TODO

## High Priority

### Core Features (User Story Aligned)

- [ ] **Thinking blocks stripped from non-streaming responses** - Causes 500 errors when `thinking` enabled. Fix `openai_to_anthropic_response()` to extract `message.thinking_blocks` and include FIRST in content array. [#128](https://github.com/LuthienResearch/luthien-proxy/issues/128)
- [ ] **Thinking blocks not handled in streaming responses** - `AnthropicSSEAssembler` doesn't recognize thinking chunk types. [#129](https://github.com/LuthienResearch/luthien-proxy/issues/129)

### Known Test Failures

- [ ] **test_gateway.sh failing for ChatGPT** - ChatGPT backend tests are failing. Needs investigation. Noted 2026-01-13.

### Policy UI & Admin

- [ ] **Improved policy config schema system** - Enhance config schema to include: default values, per-field user-facing docstrings/descriptions, and secure field flags (e.g. API keys should render as password inputs in browser). Currently the UI infers types from schema but lacks rich metadata for good UX.
- [ ] **[Future] Smart dev key hint** - Only show clickable dev key hint when ADMIN_API_KEY matches default; otherwise just show "check .env or contact admin". Deferred as scope creep. Reference: dogfooding-login-ui-quick-fixes branch, 2025-12-15.
- [ ] **Activity Monitor missing auth indicator** - Gateway root page links to Activity Monitor but doesn't show "Auth Required" indicator for consistency with other protected pages. Reference: dogfooding session 2025-12-15.

### Type System Improvements

- [x] **Break up llm/types.py into submodules** - Split into `llm/types/openai.py` and `llm/types/anthropic.py` for cleaner organization as the file grows.
- [x] **Complete strict typing for LLM types** - Add remaining TypedDict definitions for full request/response typing (OpenAIRequestDict, AnthropicRequestDict, AnthropicResponseDict, tool types).
- [x] **Move Request class to llm/types/openai.py** - Moved from top-level `messages.py` to live with other LLM types.

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

- [ ] **Verify UI monitoring endpoints functionality** - Test all debug and activity endpoints (debug endpoints have tests, UI routes do not)
- [ ] **Add rate limiting middleware** - Not blocking any user story, but useful for production
- [ ] **Implement circuit breaker for upstream calls** - Queue overflow protection exists, but not full circuit breaker pattern
- [x] **Add resource limits to docker-compose.yaml**
- [x] **Implement proper task tracking for event publisher** - Has `add_done_callback` for error logging in emitter.py

### Documentation (Medium)

- [ ] Add OpenAPI/Swagger documentation for V2 gateway
- [ ] Document production deployment best practices
- [ ] Document timeout configuration rationale
- [ ] Document data retention policy

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
